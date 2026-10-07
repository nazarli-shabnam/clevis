"""Tests for alert destinations (CRUD, test-send, score-drop dispatch, SSRF guard)."""

import hashlib
import hmac
import json
import socket
from datetime import datetime, timedelta, timezone
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
from src.repositories import scan_results_repo
from src.services.scan_service import notify_score_drop_best_effort as _notify_score_drop_best_effort
from src.services.scan_service import persist_scan_and_alert
from src.routers.notifications import router as notif_router
from src.services import notifications

HOOK = "https://hooks.slack.com/services/T000/B000/SECRETSECRET"


def _public_dns():
    return patch("src.services.notifications.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))])


def _client_cm(status_code: int):
    """Stand-in for httpx.Client(...): .stream(...) yields a response with that status."""
    client = MagicMock()
    client.stream.return_value.__enter__.return_value = httpx.Response(status_code)
    cm = MagicMock()
    cm.__enter__.return_value = client
    return cm


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


@pytest.mark.parametrize("url", ["https://hooks.example.com:notaport/x", "https://hooks.example.com:99999/x", "https://[::1/x"])
def test_validate_maps_malformed_urls_to_unsafe(url):
    with pytest.raises(notifications.UnsafeDestinationURL):
        notifications.validate_destination_url(url)


def test_validate_maps_overlong_idn_label_to_unsafe():
    with pytest.raises(notifications.UnsafeDestinationURL):
        notifications.validate_destination_url("https://" + "a" * 70 + ".example.com/hook")  # real getaddrinfo: UnicodeError


def test_create_returns_422_not_500_for_a_bad_port(db, acme):
    resp = _create(_client(db, acme["admin"]), url="https://hooks.example.com:99999/x")
    assert resp.status_code == 422


def test_post_connects_to_the_pinned_ip_but_verifies_and_names_the_hostname():
    client_cm = _client_cm(204)
    with patch("src.services.notifications.httpx.Client", return_value=client_cm) as ctor:
        status, ok = notifications._post("https://hooks.slack.com:8443/a?b=1", "8.8.8.8", b"{}", {"X-A": "1"})
    assert (status, ok) == (204, True)
    assert ctor.call_args.kwargs["follow_redirects"] is False
    args, kwargs = client_cm.__enter__.return_value.stream.call_args
    assert args == ("POST", "https://8.8.8.8:8443/a?b=1")
    assert kwargs["headers"]["Host"] == "hooks.slack.com:8443"
    assert kwargs["extensions"] == {"sni_hostname": "hooks.slack.com"}


def test_pin_prefers_ipv4_over_an_earlier_ipv6_record():
    infos = [(socket.AF_INET6, 1, 6, "", ("2001:4860::1", 443, 0, 0)), (socket.AF_INET, 1, 6, "", ("8.8.8.8", 443))]
    with patch("src.services.notifications.socket.getaddrinfo", return_value=infos):
        assert notifications._resolve_public_ip(HOOK) == "8.8.8.8"


def test_post_brackets_ipv6_pins():
    client_cm = _client_cm(200)
    with patch("src.services.notifications.httpx.Client", return_value=client_cm):
        notifications._post("https://hooks.example.com/x", "2001:4860::1", b"{}", {})
    assert client_cm.__enter__.return_value.stream.call_args.args[1] == "https://[2001:4860::1]/x"


def test_test_send_is_rate_limited_per_org(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]
    with _public_dns(), patch("src.services.notifications._post", return_value=(200, True)):
        codes = [client.post(f"/orgs/acme/notification-destinations/{dest_id}/test").status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


# --- payloads / signing ---

def test_payload_shapes_per_kind():
    assert notifications.build_payload("slack", "score_drop", "hi", {}) == {"text": "hi"}
    teams = notifications.build_payload("teams", "score_drop", "hi", {})
    assert teams["attachments"][0]["content"]["body"][0]["text"] == "hi"
    generic = notifications.build_payload("generic", "score_drop", "hi", {"a": 1})
    assert generic["event"] == "score_drop" and generic["data"] == {"a": 1}


def test_signature_is_hmac_sha256_hex_of_the_body():
    body = b'{"x":1}'
    expected = hmac.new(b"s3cret", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert notifications.sign("s3cret", body, "1700000000") == "sha256=" + expected
    assert notifications.sign("s3cret", body, "1700000001") != "sha256=" + expected  # timestamp is covered


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

    with _public_dns(), patch("src.services.notifications._post", return_value=(200, True)) as post:
        resp = client.post(f"/orgs/acme/notification-destinations/{dest_id}/test")

    assert resp.json() == {"ok": True, "detail": "Delivered"}
    url, ip, body, headers = post.call_args.args
    assert (url, ip) == (HOOK, "8.8.8.8")
    assert headers["X-Clevis-Signature"] == notifications.sign("topsecret", body, headers["X-Clevis-Timestamp"])
    log = db.query(AuditLog).filter(AuditLog.action == "notification.test_sent").one()
    assert HOOK not in log.payload


def test_test_send_failure_reports_a_reason_without_leaking_the_url(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]

    with _public_dns(), patch(
        "src.services.notifications._post", side_effect=httpx.ConnectError(f"cannot reach {HOOK}")
    ):
        resp = client.post(f"/orgs/acme/notification-destinations/{dest_id}/test")

    assert resp.json() == {"ok": False, "detail": "Delivery failed; see the audit log"}
    assert "request failed" in db.query(AuditLog).filter(AuditLog.action == "notification.test_sent").one().payload


def test_test_send_non_2xx_is_a_failure(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client).json()["id"]
    with _public_dns(), patch("src.services.notifications._post", return_value=(404, False)):
        resp = client.post(f"/orgs/acme/notification-destinations/{dest_id}/test").json()
    # The target's status code is not echoed back (status-code oracle); it is only audit-logged.
    assert resp == {"ok": False, "detail": "Delivery failed; see the audit log"}
    assert "HTTP 404" in db.query(AuditLog).filter(AuditLog.action == "notification.test_sent").one().payload


def test_send_revalidates_the_url_at_delivery_time(db, acme):
    dest_id = _create(_client(db, acme["admin"])).json()["id"]
    dest = db.get(NotificationDestination, dest_id)
    # DNS now points at an internal address: the stored destination must be refused, not called.
    with patch("src.services.notifications.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.9", 443))]), patch(
        "src.services.notifications._post"
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


def test_score_drop_respects_cooldown_and_releases_failed_claims(db, acme):
    org = acme["org"]
    dest = _dest(db, org)
    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        notifications.notify_score_drop(db, org.tenant_id, "acme", previous=90, current=70)
        notifications.notify_score_drop(db, org.tenant_id, "acme", previous=90, current=60)  # inside cooldown
    assert send.call_count == 1

    dest.last_notified_at = datetime.now(timezone.utc) - notifications.ALERT_COOLDOWN - timedelta(minutes=1)
    db.commit()
    with patch("src.services.notifications.send", return_value=(False, "request failed")) as send:
        notifications.notify_score_drop(db, org.tenant_id, "acme", previous=90, current=70)
    assert send.call_count == 1
    db.refresh(dest)
    assert dest.last_notified_at is None  # failed delivery doesn't burn the cooldown


def test_best_effort_wrapper_swallows_errors(db, acme):
    with patch("src.routers.analytics.notifications.notify_score_drop", side_effect=RuntimeError("boom")):
        _notify_score_drop_best_effort(db, acme["org"], 90, 50)  # must not raise


def test_settings_key_is_available_for_encryption():
    assert settings.job_secret_key.get_secret_value()


# --- persist_scan_and_alert (shared by the session and API-token scan paths) ---

def _chk(check_id, status="pass", **extra):
    return {"id": check_id, "title": check_id, "status": status, "value": {}, **extra}


def _result(score, checks, owner="acme"):
    return {"owner": owner, "score": score, "total_checks": len(checks), "failed_checks": 0, "checks": checks}


def _seed(db, org, score, checks, owner="acme"):
    scan_results_repo.insert(
        db, owner=owner, score=score, total_checks=len(checks), failed_checks=0, checks=checks, tenant_id=org.tenant_id
    )


def _run_persist(db, org, result):
    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        persist_scan_and_alert(db, org, result, org.tenant_id)
    return send


def test_baseline_matches_owner_case_insensitively(db, acme):
    org = acme["org"]
    _dest(db, org)
    checks = [_chk("a"), _chk("b")]
    _seed(db, org, 90, checks, owner="ACME")  # stored by a scan typed in another casing
    assert _run_persist(db, org, _result(70, checks)).call_count == 1


def test_no_alert_when_either_scan_has_errored_checks(db, acme):
    org = acme["org"]
    _dest(db, org)
    _seed(db, org, 90, [_chk("a"), _chk("b")])
    assert _run_persist(db, org, _result(50, [_chk("a"), _chk("b", "error")])).call_count == 0
    # an errored *previous* scan is not a trustworthy baseline either
    assert _run_persist(db, org, _result(10, [_chk("a", "fail"), _chk("b", "fail")])).call_count == 0


def test_no_alert_when_the_scored_check_set_changed(db, acme):
    org = acme["org"]
    _dest(db, org)
    _seed(db, org, 90, [_chk("a"), _chk("b")])
    now = [_chk("a"), _chk("b"), _chk("hygiene", "fail")]  # score_hygiene_checks toggled on
    assert _run_persist(db, org, _result(60, now)).call_count == 0
    # an unscored (informational) extra check doesn't change the basis
    _seed(db, org, 90, [_chk("a"), _chk("b")])
    now = [_chk("a"), _chk("b"), _chk("hygiene", "fail", scored=False)]
    assert _run_persist(db, org, _result(60, now)).call_count == 1


def test_unstamped_informational_checks_fall_back_to_unscored(db, acme):
    org = acme["org"]
    _dest(db, org)
    # legacy rows carry no ``scored`` stamp: informational ones are unscored, the rest scored
    _seed(db, org, 90, [_chk("a"), _chk("b")])
    now = [_chk("a"), _chk("b"), _chk("hygiene", "fail", informational=True)]
    assert _run_persist(db, org, _result(60, now)).call_count == 1
    # an unstamped non-informational extra check is scored, so the basis changed
    _seed(db, org, 90, [_chk("a"), _chk("b")])
    now = [_chk("a"), _chk("b"), _chk("extra", "fail")]
    assert _run_persist(db, org, _result(60, now)).call_count == 0


def test_explicit_scored_stamp_wins_over_informational(db, acme):
    org = acme["org"]
    _dest(db, org)
    # hygiene scoring on: an informational check stamped scored=True is part of the basis
    _seed(db, org, 90, [_chk("a"), _chk("h", informational=True, scored=True)])
    now = [_chk("a"), _chk("h", "fail", informational=True, scored=True)]
    assert _run_persist(db, org, _result(60, now)).call_count == 1
    _seed(db, org, 90, [_chk("a"), _chk("h", informational=True, scored=True)])
    now = [_chk("a"), _chk("h", "fail", informational=True, scored=False)]
    assert _run_persist(db, org, _result(60, now)).call_count == 0
