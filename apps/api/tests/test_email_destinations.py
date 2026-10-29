"""Email as an alert destination: recipient rules, SMTP gating, delivery, audit."""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import AuditLog, NotificationDestination, User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.notifications import router
from src.services import notifications

SMTP_ON = patch("src.services.email.is_configured", return_value=True)


def _user(db, email, *, verified=True):
    u = User(email=email, name=None, password_hash=None, is_workspace_admin=False, email_verified=verified)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture()
def acme(db):
    admin = _user(db, "mail-admin@e.com")
    member = _user(db, "mail-member@e.com")
    unverified = _user(db, "mail-unverified@e.com", verified=False)
    outsider = _user(db, "mail-outsider@e.com")
    org = org_repo.get_or_create(db, github_login="acme-mail")
    for u, role in ((admin, "admin"), (member, "member"), (unverified, "member")):
        org_membership_repo.get_or_create(db, org_id=org.id, user_id=u.id, role=role)
    return {"org": org, "admin": admin, "member": member, "unverified": unverified, "outsider": outsider}


def _client(db, user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _create(client, recipients, **over):
    body = {"kind": "email", "name": "oncall", "recipients": recipients, **over}
    with SMTP_ON:
        return client.post("/orgs/acme-mail/notification-destinations", json=body)


def test_creates_an_email_destination_for_verified_members_and_stores_the_addresses_encrypted(db, acme):
    resp = _create(_client(db, acme["admin"]), ["Mail-Member@e.com", "mail-admin@e.com", "mail-member@e.com"])
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "email" and body["recipients"] == ["mail-admin@e.com", "mail-member@e.com"]
    assert "url" not in body and body["signed"] is False
    row = db.get(NotificationDestination, body["id"])
    assert "mail-member@e.com" not in row.encrypted_url and row.encrypted_url.startswith("v2:")
    log = db.query(AuditLog).filter(AuditLog.action == "notification.destination_created").one()
    assert json.loads(log.payload)["recipient_count"] == 2 and "mail-member@e.com" not in log.payload


def test_recipients_must_be_verified_members_of_the_org(db, acme):
    client = _client(db, acme["admin"])
    for bad in ("mail-outsider@e.com", "mail-unverified@e.com", "nobody@example.org"):
        resp = _create(client, ["mail-member@e.com", bad])
        assert resp.status_code == 422 and bad in resp.json()["detail"]
    assert db.query(NotificationDestination).count() == 0


def test_creation_is_refused_without_smtp_and_with_a_bad_shape(db, acme):
    client = _client(db, acme["admin"])
    with patch("src.services.email.is_configured", return_value=False):
        resp = client.post(
            "/orgs/acme-mail/notification-destinations",
            json={"kind": "email", "name": "x", "recipients": ["mail-member@e.com"]},
        )
    assert resp.status_code == 422 and "SMTP" in resp.json()["detail"]
    assert _create(client, []).status_code == 422
    assert _create(client, ["not-an-address"]).status_code == 422
    assert _create(client, ["mail-member@e.com"], url="https://hooks.slack.com/x").status_code == 422
    assert client.post("/orgs/acme-mail/notification-destinations", json={"kind": "slack", "name": "x"}).status_code == 422


def test_only_admins_manage_email_destinations(db, acme):
    assert _create(_client(db, acme["member"]), ["mail-member@e.com"]).status_code == 403


def test_test_send_emails_each_recipient_and_audits_without_addresses(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client, ["mail-admin@e.com", "mail-member@e.com"]).json()["id"]
    with SMTP_ON, patch("src.services.email.send_email") as send:
        resp = client.post(f"/orgs/acme-mail/notification-destinations/{dest_id}/test")
    assert resp.json() == {"ok": True, "detail": "Delivered"}
    assert sorted(c.args[0] for c in send.call_args_list) == ["mail-admin@e.com", "mail-member@e.com"]
    assert "acme-mail" in send.call_args.args[1]  # subject names the org
    log = db.query(AuditLog).filter(AuditLog.action == "notification.test_sent").one()
    assert "mail-member@e.com" not in log.payload


def test_a_recipient_who_is_no_longer_a_verified_member_is_skipped_at_send_time(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client, ["mail-admin@e.com", "mail-member@e.com"]).json()["id"]
    member = db.get(User, acme["member"].id)
    member.email_verified = False
    db.commit()
    with SMTP_ON, patch("src.services.email.send_email") as send:
        client.post(f"/orgs/acme-mail/notification-destinations/{dest_id}/test")
    assert [c.args[0] for c in send.call_args_list] == ["mail-admin@e.com"]


def test_a_failed_send_is_reported_and_audited_as_failed(db, acme):
    client = _client(db, acme["admin"])
    dest_id = _create(client, ["mail-admin@e.com"]).json()["id"]
    with SMTP_ON, patch("src.services.email.send_email", side_effect=OSError("smtp down")):
        resp = client.post(f"/orgs/acme-mail/notification-destinations/{dest_id}/test")
    assert resp.json()["ok"] is False
    log = db.query(AuditLog).filter(AuditLog.action == "notification.test_sent").one()
    assert json.loads(log.payload)["ok"] is False
    # And with SMTP gone entirely there is a clear reason rather than a crash.
    dest = db.get(NotificationDestination, dest_id)
    with patch("src.services.email.is_configured", return_value=False):
        assert notifications.send(dest, "test", "t", {"org": "acme-mail"}, email_allowed={"mail-admin@e.com"}) == (
            False,
            "SMTP is not configured",
        )


def test_score_drop_and_waiver_alerts_reach_email_destinations(db, acme):
    org = acme["org"]
    client = _client(db, acme["admin"])
    _create(client, ["mail-member@e.com"], events=["score_drop", "waiver_expired"])
    with SMTP_ON, patch("src.services.email.send_email") as send:
        notifications.notify_score_drop(db, org.tenant_id, "acme-mail", previous=90, current=60)
        notifications.notify_waiver_expired(
            db, org.tenant_id, "acme-mail", [{"check_id": "repository_license_present", "repos": None, "owner": "o", "reason": "r"}]
        )
    subjects = [c.args[1] for c in send.call_args_list]
    assert subjects == ["Clevis: security score dropped for acme-mail", "Clevis: check waivers expired for acme-mail"]
    assert "dropped 30 points" in send.call_args_list[0].args[2]
    sent = db.query(AuditLog).filter(AuditLog.action == "notification.sent").all()
    assert len(sent) == 2 and all(json.loads(a.payload)["kind"] == "email" for a in sent)
    assert settings is not None  # (settings import keeps the module's encryption key resolvable)
