"""Tests for the destination PATCH route and the new_critical_alert / stale_pr_summary /
automation_failed alert events."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core._crypto import decrypt_job_token
from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import AuditLog, NotificationDestination, SecurityAlert, User, get_db
from src.repositories import notification_repo, org_membership_repo, org_repo
from src.routers.notifications import router as notif_router
from src.services import notifications
from src.services.security_alert_sweep import run_security_alert_sweep

HOOK = "https://hooks.slack.com/services/T000/B000/SECRETSECRET"
NEW_HOOK = "https://hooks.slack.com/services/T111/B111/ROTATED"


def _public_dns():
    return patch("src.services.notifications.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))])


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(notif_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


@pytest.fixture()
def acme(db):
    admin = _make_user(db, "events-admin@e.com")
    member = _make_user(db, "events-member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def _dest(db, org, *, events=("score_drop",), enabled=True):
    return notification_repo.create(
        db, tenant_id=org.tenant_id, kind="slack", name="n", encrypted_url="v2:x", events=list(events), enabled=enabled
    )


def _create(client, **overrides):
    body = {"kind": "slack", "name": "alerts", "url": HOOK, **overrides}
    with _public_dns():
        return client.post("/orgs/acme/notification-destinations", json=body)


# --- PATCH ---

def test_patch_updates_enabled_events_threshold_and_audits_without_secrets(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]
    resp = client.patch(
        f"/orgs/acme/notification-destinations/{dest_id}",
        json={"enabled": False, "events": ["new_critical_alert", "automation_failed"], "min_score_drop": 25, "name": "renamed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False and body["min_score_drop"] == 25 and body["name"] == "renamed"
    assert body["events"] == ["new_critical_alert", "automation_failed"]
    log = db.query(AuditLog).filter(AuditLog.action == "notification.destination_updated").one()
    payload = json.loads(log.payload)
    assert sorted(payload["changed"]) == ["enabled", "events", "min_score_drop", "name"]
    assert HOOK not in log.payload


def test_patch_rotates_url_after_ssrf_check_and_rejects_unsafe_url(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]
    with _public_dns():
        ok = client.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"url": NEW_HOOK})
    assert ok.status_code == 200
    dest = db.get(NotificationDestination, dest_id)
    db.refresh(dest)
    assert decrypt_job_token(dest.encrypted_url, settings.job_secret_key.get_secret_value()) == NEW_HOOK
    bad = client.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"url": "http://169.254.169.254/x"})
    assert bad.status_code == 422


def test_patch_rejects_fields_that_do_not_fit_the_kind(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]  # slack
    assert client.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"secret": "s3cret"}).status_code == 422
    assert (
        client.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"recipients": ["a@e.com"]}).status_code
        == 422
    )


def test_patch_rejects_an_empty_signing_secret(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client, kind="generic", url="https://example.com/hook", secret="s3cret").json()["id"]
    resp = client.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"secret": ""})
    assert resp.status_code == 422
    dest = db.get(NotificationDestination, dest_id)
    db.refresh(dest)
    assert decrypt_job_token(dest.encrypted_secret, settings.job_secret_key.get_secret_value()) == "s3cret"


def test_every_event_has_its_own_email_subject():
    assert all(event in notifications._EMAIL_SUBJECTS for event in notifications.EVENTS)


def test_patch_rejects_unknown_event_and_missing_destination_and_non_admin(db, acme):
    admin = _client(db, acme["admin"])
    dest_id = _create(admin).json()["id"]
    assert admin.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"events": ["nope"]}).status_code == 422
    assert admin.patch("/orgs/acme/notification-destinations/999999", json={"enabled": False}).status_code == 404
    member = _client(db, acme["member"])
    assert member.patch(f"/orgs/acme/notification-destinations/{dest_id}", json={"enabled": False}).status_code == 403


# --- notifier functions ---

def test_new_critical_alert_goes_only_to_enabled_subscribers(db, acme):
    org = acme["org"]
    hit = _dest(db, org, events=("new_critical_alert",))
    _dest(db, org, events=("score_drop",))
    _dest(db, org, events=("new_critical_alert",), enabled=False)
    alerts = [{"repo": "api", "kind": "dependabot", "number": 3, "severity": "critical"}]
    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        notifications.notify_new_critical_alerts(db, org.tenant_id, "acme", alerts)
    assert [c.args[0].id for c in send.call_args_list] == [hit.id]
    assert "api (dependabot #3)" in send.call_args.args[2]
    assert db.query(AuditLog).filter(AuditLog.action == "notification.sent").count() == 1


@pytest.mark.parametrize(
    ("func", "event", "items"),
    [
        ("notify_stale_prs", "stale_pr_summary", [{"number": 4, "title": "t", "action": "commented"}]),
        ("notify_automation_failed", "automation_failed", [{"workflow": "ci", "message": "boom"}]),
    ],
)
def test_other_events_reach_subscribers_and_empty_lists_send_nothing(db, acme, func, event, items):
    org = acme["org"]
    hit = _dest(db, org, events=(event,))
    _dest(db, org, events=("score_drop",))
    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        getattr(notifications, func)(db, org.tenant_id, "acme", "api", [])
        send.assert_not_called()
        getattr(notifications, func)(db, org.tenant_id, "acme", "api", items)
    assert [c.args[0].id for c in send.call_args_list] == [hit.id]


# --- critical alert sweep ---

def _alert(db, org, number, *, kind="dependabot", severity="critical", state="open", notified=False):
    now = datetime.now(timezone.utc)
    db.add(
        SecurityAlert(
            tenant_id=org.tenant_id, repo="api", kind=kind, number=number, state=state, severity=severity,
            created_at=now, updated_at=now, alert_notified_at=now if notified else None,
        )
    )
    db.commit()


def test_sweep_announces_each_new_critical_alert_once(db, acme):
    org = acme["org"]
    _alert(db, org, 1)
    _alert(db, org, 2, severity="high")
    _alert(db, org, 3, kind="secret_scanning", severity=None)
    _alert(db, org, 4, severity="medium")
    _alert(db, org, 5, state="dismissed")
    _alert(db, org, 6, notified=True)
    with patch("src.services.security_alert_sweep.notifications.notify_new_critical_alerts") as notify:
        run_security_alert_sweep(db)
        run_security_alert_sweep(db)  # second tick: nothing left to announce
    assert notify.call_count == 1
    announced = notify.call_args.args[3]
    assert sorted(a["number"] for a in announced) == [1, 2, 3]
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    unstamped = db.execute(
        text("SELECT number FROM security_alerts WHERE tenant_id = :t AND alert_notified_at IS NULL ORDER BY number"),
        {"t": org.tenant_id},
    ).scalars().all()
    assert unstamped == [4, 5]  # medium and dismissed stay eligible should they escalate or reopen


def test_a_failing_alert_delivery_does_not_stop_the_sweep_or_repeat(db, acme):
    org = acme["org"]
    _alert(db, org, 1)
    with patch(
        "src.services.security_alert_sweep.notifications.notify_new_critical_alerts", side_effect=RuntimeError("x")
    ) as notify:
        run_security_alert_sweep(db)
        run_security_alert_sweep(db)
    assert notify.call_count == 1


def test_github_derived_names_cannot_inject_chat_markup(db, acme):
    org = acme["org"]
    _dest(db, org, events=("automation_failed", "new_critical_alert"))
    nasty = "<!channel> <https://evil.example|click> [x](https://evil.example) *bold*\nnewline"
    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        notifications.notify_automation_failed(db, org.tenant_id, "acme", "api", [{"workflow": nasty, "message": "m"}])
        notifications.notify_new_critical_alerts(
            db, org.tenant_id, "acme", [{"repo": nasty, "kind": "secret_scanning", "number": 1, "severity": None}]
        )
    for call in send.call_args_list:
        text_sent = call.args[2]
        assert "<" not in text_sent and ">" not in text_sent and "[" not in text_sent and "\n" not in text_sent
        assert "&lt;!channel&gt;" in text_sent
    # the structured data for generic webhooks keeps the raw value
    assert send.call_args_list[0].args[3]["failures"][0]["workflow"] == nasty
    assert "secret scanning #1" in send.call_args_list[1].args[2]

