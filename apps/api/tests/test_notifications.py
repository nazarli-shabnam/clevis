"""Tests for alert destinations (CRUD, test-send, score-drop dispatch, SSRF guard)."""

import hashlib
import hmac
import json
import socket
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import AuditLog, NotificationDestination, User, get_db
from src.repositories import notification_repo, org_membership_repo, org_repo
from src.routers.analytics import _notify_score_drop_best_effort
from src.routers.notifications import router as notif_router
from src.services import notifications

HOOK = "https://hooks.slack.com/services/T000/B000/SECRETSECRET"


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
    admin = _make_user(db, "notif-admin@e.com")
    member = _make_user(db, "notif-member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def _create(client, **overrides):
    body = {"kind": "slack", "name": "alerts", "url": HOOK, **overrides}
    with _public_dns():
        return client.post("/orgs/acme/notification-destinations", json=body)


# --- SSRF guard ---

@pytest.mark.parametrize(
    "url",
    ["http://hooks.slack.com/x", "https://user:pw@hooks.slack.com/x", "ftp://hooks.slack.com/x", "https:///nohost"],
)
def test_validate_rejects_non_https_and_credentialed_urls(url):
    with pytest.raises(notifications.UnsafeDestinationURL):
        notifications.validate_destination_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "fd00::1"])
def test_validate_rejects_hosts_resolving_to_non_public_addresses(ip):
    with patch("src.services.notifications.socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 443))]):
        with pytest.raises(notifications.UnsafeDestinationURL):
            notifications.validate_destination_url("https://internal.example.com/hook")


def test_validate_rejects_when_any_resolved_address_is_private():
    infos = [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("10.0.0.1", 443))]
    with patch("src.services.notifications.socket.getaddrinfo", return_value=infos):
        with pytest.raises(notifications.UnsafeDestinationURL):
            notifications.validate_destination_url("https://mixed.example.com/hook")


def test_validate_rejects_unresolvable_host():
    with patch("src.services.notifications.socket.getaddrinfo", side_effect=socket.gaierror):
        with pytest.raises(notifications.UnsafeDestinationURL):
            notifications.validate_destination_url("https://nope.invalid/hook")


def test_validate_accepts_public_https_url():
    with _public_dns():
        notifications.validate_destination_url(HOOK)


# --- payloads / signing ---

def test_payload_shapes_per_kind():
    assert notifications.build_payload("slack", "score_drop", "hi", {}) == {"text": "hi"}
    teams = notifications.build_payload("teams", "score_drop", "hi", {})
    assert teams["attachments"][0]["content"]["body"][0]["text"] == "hi"
    generic = notifications.build_payload("generic", "score_drop", "hi", {"a": 1})
    assert generic["event"] == "score_drop" and generic["data"] == {"a": 1}


def test_signature_is_hmac_sha256_hex_of_the_body():
    body = b'{"x":1}'
    assert notifications.sign("s3cret", body) == "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()


# --- CRUD ---

def test_create_stores_encrypted_url_and_never_returns_it(db, acme):
    resp = _create(_client(db, acme["admin"]), secret="topsecret", kind="generic")

    assert resp.status_code == 201
    body = resp.json()
    assert "url" not in body and "secret" not in body and "encrypted_url" not in body
    assert body["signed"] is True and body["events"] == ["score_drop"] and body["min_score_drop"] == 10
    row = db.get(NotificationDestination, body["id"])
    assert HOOK not in row.encrypted_url and row.encrypted_url.startswith("v2:")
    assert "topsecret" not in (row.encrypted_secret or "")
    log = db.query(AuditLog).filter(AuditLog.action == "notification.destination_created").one()
    assert HOOK not in log.payload


def test_create_requires_admin(db, acme):
    assert _create(_client(db, acme["member"])).status_code == 403


def test_create_rejects_unsafe_url_with_422(db, acme):
    client = _client(db, acme["admin"])
    resp = client.post("/orgs/acme/notification-destinations", json={"kind": "slack", "name": "x", "url": "http://hooks.slack.com/x"})
    assert resp.status_code == 422


def test_secret_is_only_kept_for_generic_destinations(db, acme):
    body = _create(_client(db, acme["admin"]), kind="slack", secret="ignored").json()
    assert body["signed"] is False


def test_create_caps_destinations_per_org(db, acme):
    client = _client(db, acme["admin"])
    for i in range(10):
        assert _create(client, name=f"d{i}").status_code == 201
    assert _create(client, name="one-too-many").status_code == 409


def test_list_and_delete_are_scoped_to_the_org(db, acme):
    client = _client(db, acme["admin"])
    created = _create(client).json()
    assert [d["id"] for d in client.get("/orgs/acme/notification-destinations").json()] == [created["id"]]

    assert client.delete("/orgs/acme/notification-destinations/999999").status_code == 404
    assert client.delete(f"/orgs/acme/notification-destinations/{created['id']}").status_code == 204
    assert client.get("/orgs/acme/notification-destinations").json() == []
    assert db.query(AuditLog).filter(AuditLog.action == "notification.destination_deleted").count() == 1


# --- test-send ---

def test_test_send_posts_signed_body_and_audits_without_the_url(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client, kind="generic", secret="topsecret").json()["id"]

    with _public_dns(), patch("src.services.notifications.httpx.post", return_value=httpx.Response(200)) as post:
        resp = client.post(f"/orgs/acme/notification-destinations/{dest_id}/test")

    assert resp.json() == {"ok": True, "detail": "HTTP 200"}
    kwargs = post.call_args.kwargs
    assert post.call_args.args[0] == HOOK
    assert kwargs["follow_redirects"] is False
    assert kwargs["headers"]["X-Clevis-Signature"] == notifications.sign("topsecret", kwargs["content"])
    log = db.query(AuditLog).filter(AuditLog.action == "notification.test_sent").one()
    assert HOOK not in log.payload


def test_test_send_failure_reports_a_reason_without_leaking_the_url(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]

    with _public_dns(), patch(
        "src.services.notifications.httpx.post", side_effect=httpx.ConnectError(f"cannot reach {HOOK}")
    ):
        resp = client.post(f"/orgs/acme/notification-destinations/{dest_id}/test")

    assert resp.json() == {"ok": False, "detail": "request failed"}


def test_test_send_non_2xx_is_a_failure(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]
    with _public_dns(), patch("src.services.notifications.httpx.post", return_value=httpx.Response(404)):
        assert client.post(f"/orgs/acme/notification-destinations/{dest_id}/test").json() == {"ok": False, "detail": "HTTP 404"}


def test_send_revalidates_the_url_at_delivery_time(db, acme):
    dest_id = _create(_client(db, acme["admin"])).json()["id"]
    dest = db.get(NotificationDestination, dest_id)
    # DNS now points at an internal address: the stored destination must be refused, not called.
    with patch("src.services.notifications.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.9", 443))]), patch(
        "src.services.notifications.httpx.post"
    ) as post:
        ok, detail = notifications.send(dest, "test", "hi")
    assert ok is False and "public address" in detail
    post.assert_not_called()


# --- score drop ---

def _dest(db, org, *, events=("score_drop",), min_drop=10, enabled=True):
    return notification_repo.create(
        db,
        tenant_id=org.tenant_id,
        kind="slack",
        name="n",
        encrypted_url="v2:x",
        events=list(events),
        min_score_drop=min_drop,
        enabled=enabled,
    )


def test_score_drop_notifies_only_matching_destinations_and_audits(db, acme):
    org = acme["org"]
    hit = _dest(db, org)
    _dest(db, org, min_drop=30)  # threshold not met
    _dest(db, org, enabled=False)
    _dest(db, org, events=())

    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        notifications.notify_score_drop(db, org.tenant_id, "acme", previous=90, current=70)

    assert [c.args[0].id for c in send.call_args_list] == [hit.id]
    assert "dropped 20 points" in send.call_args.args[2]
    logs = db.query(AuditLog).filter(AuditLog.action == "notification.sent").all()
    assert len(logs) == 1 and json.loads(logs[0].payload)["ok"] is True


def test_no_notification_when_score_did_not_drop(db, acme):
    _dest(db, acme["org"])
    with patch("src.services.notifications.send") as send:
        notifications.notify_score_drop(db, acme["org"].tenant_id, "acme", previous=70, current=70)
        notifications.notify_score_drop(db, acme["org"].tenant_id, "acme", previous=70, current=95)
    send.assert_not_called()


def test_best_effort_wrapper_swallows_errors(db, acme):
    ctx = MagicMock()
    ctx.org = acme["org"]
    with patch("src.routers.analytics.notifications.notify_score_drop", side_effect=RuntimeError("boom")):
        _notify_score_drop_best_effort(db, ctx, 90, 50)  # must not raise


def test_settings_key_is_available_for_encryption():
    assert settings.job_secret_key.get_secret_value()
