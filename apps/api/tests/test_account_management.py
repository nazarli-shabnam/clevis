"""Changing the account email (confirmed through a link to the new address) and deleting one's own account."""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import create_access_token
from src.core.config import settings
from src.core.db import AuditLog, Membership, ScanResult, Tenant, User, UserSession, get_db, set_session_tenant
from src.core.rate_limit import _account_buckets as _account_rate_limit_buckets
from src.core.rate_limit import _buckets as _rate_limit_buckets
from src.repositories import invitation_repo, org_membership_repo, org_repo
from src.routers.auth import router as auth_router
from src.services import email_change, password_reset


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    _rate_limit_buckets.clear()
    _account_rate_limit_buckets.clear()
    yield
    _rate_limit_buckets.clear()
    _account_rate_limit_buckets.clear()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _setup_owner(client):
    resp = client.post("/auth/setup", json={"email": "owner@example.com", "password": "supersecret1234"})
    assert resp.status_code == 201
    return resp.json()


def _register(client, email="member@example.com"):
    resp = client.post("/auth/register", json={"email": email, "password": "supersecret1234"})
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _smtp_on(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "clevis@example.com")


def _rows(db, action):
    return db.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()


def _github_only_headers(db):
    user = User(email="gh-only@example.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    return user.id, {"Authorization": f"Bearer {create_access_token(user.id, user.email, False)}"}


# --- change email ---------------------------------------------------------------------------------------

def test_email_change_is_confirmed_through_a_link_sent_to_the_new_address(client, db, monkeypatch):
    _setup_owner(client)
    headers = _register(client)
    _smtp_on(monkeypatch)
    with patch("src.routers.auth.send_email_change_email") as send:
        resp = client.post("/auth/me/email", headers=headers, json={"new_email": "New@Example.com", "password": "supersecret1234"})
    assert resp.status_code == 200
    assert send.call_args.args[0] == "new@example.com" and "/confirm-email-change?token=" in send.call_args.args[1]
    token = send.call_args.args[1].split("token=", 1)[1]
    # Nothing changes until the link is opened.
    assert db.query(User).filter(User.email == "member@example.com").one().email_verified is False

    assert client.post("/auth/confirm-email-change", json={"token": token}).status_code == 200
    db.expire_all()
    changed = db.query(User).filter(User.email == "new@example.com").one()
    assert changed.email_verified is True and changed.token_version == 1
    payload = json.loads(_rows(db, "auth.email_changed")[0].payload)
    assert payload["from"] == "member@example.com" and payload["to"] == "new@example.com"

    assert client.get("/auth/me", headers=headers).status_code == 401  # signed out: session tokens carried the old email
    assert client.post("/auth/login", json={"email": "new@example.com", "password": "supersecret1234"}).status_code == 200
    assert client.post("/auth/login", json={"email": "member@example.com", "password": "supersecret1234"}).status_code == 401
    assert client.post("/auth/confirm-email-change", json={"token": token}).status_code == 400  # single use


def test_email_change_requests_are_checked(client, db, monkeypatch):
    _setup_owner(client)
    headers = _register(client)
    body = {"new_email": "new@example.com", "password": "supersecret1234"}

    assert client.post("/auth/me/email", headers=headers, json=body).status_code == 409  # no SMTP: no link can be sent
    _smtp_on(monkeypatch)
    with patch("src.routers.auth.send_email_change_email") as send:
        assert client.post("/auth/me/email", headers=headers, json={**body, "password": "wrong-password-1"}).status_code == 400
        assert client.post("/auth/me/email", headers=headers, json={**body, "new_email": "MEMBER@example.com"}).status_code == 400
        assert client.post("/auth/me/email", headers=headers, json={**body, "new_email": "Owner@example.com"}).status_code == 409
        _, gh_headers = _github_only_headers(db)
        assert client.post("/auth/me/email", headers=gh_headers, json=body).status_code == 400
    send.assert_not_called()


def test_email_change_links_are_rejected_when_forged_or_taken(client, db):
    _setup_owner(client)
    headers = _register(client)
    member = db.query(User).filter(User.email == "member@example.com").one()

    assert client.post("/auth/confirm-email-change", json={"token": "garbage"}).status_code == 400
    # A password-reset token is not an email-change token.
    reset = password_reset.make_token(member.id, member.password_hash, member.email)
    assert client.post("/auth/confirm-email-change", json={"token": reset}).status_code == 400
    # The address was taken between the request and the click.
    taken = email_change.make_token(member, "owner@example.com")
    assert client.post("/auth/confirm-email-change", json={"token": taken}).status_code == 409
    assert db.query(User).filter(User.id == member.id).one().email == "member@example.com"
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_a_reset_link_issued_before_an_email_change_stops_working(client, db):
    _setup_owner(client)
    _register(client)
    member = db.query(User).filter(User.email == "member@example.com").one()
    reset = password_reset.make_token(member.id, member.password_hash, member.email)
    member.email = "moved@example.com"
    db.flush()
    r = client.post("/auth/reset-password", json={"token": reset, "password": "brand-new-pass-1"})
    assert r.status_code == 400


def test_a_pending_email_change_is_cancelled_by_a_password_reset_or_sign_out_everywhere(client, db):
    _setup_owner(client)
    headers = _register(client)
    member = db.query(User).filter(User.email == "member@example.com").one()
    token = email_change.make_token(member, "new@example.com")
    assert client.post("/auth/me/password", headers=headers, json={"current_password": "supersecret1234", "new_password": "a-brand-new-password"}).status_code == 200
    assert client.post("/auth/confirm-email-change", json={"token": token}).status_code == 400

    db.expire_all()
    member = db.query(User).filter(User.email == "member@example.com").one()
    token = email_change.make_token(member, "new@example.com")
    member.token_version += 1  # signed out everywhere
    db.commit()
    assert client.post("/auth/confirm-email-change", json={"token": token}).status_code == 400
    assert db.query(User).filter(User.email == "member@example.com").first() is not None


# --- delete account -------------------------------------------------------------------------------------

def _org_with(db, *members):
    org = org_repo.get_or_create(db, github_login="acme-del")
    for user_id, role in members:
        org_membership_repo.get_or_create(db, org_id=org.id, user_id=user_id, role=role, source="invite")
    return org


def test_a_member_deletes_their_account_and_what_hangs_off_it(client, db):
    owner = _setup_owner(client)
    headers = _register(client)
    member_id = db.query(User).filter(User.email == "member@example.com").one().id
    org = _org_with(db, (owner["user"]["id"], "admin"), (member_id, "member"))
    org_tenant = org.tenant_id
    invitation_repo.create(db, org_id=org.id, email="friend@example.com", invited_by_user_id=member_id)
    db.add(
        ScanResult(
            owner="acme-del", score=90, total_checks=1, failed_checks=0, checks_json="[]",
            tenant_id=org_tenant, scanned_by_user_id=member_id,
        )
    )
    db.commit()
    personal = db.query(Tenant).filter(Tenant.personal_user_id == member_id).one().id

    resp = client.post("/auth/me/delete", headers=headers, json={"confirm_email": "Member@Example.com", "password": "supersecret1234"})
    assert resp.status_code == 204

    db.expire_all()
    assert db.query(User).filter(User.id == member_id).first() is None
    assert db.query(Tenant).filter(Tenant.id == personal).first() is None
    assert db.query(Membership).filter(Membership.user_id == member_id).count() == 0
    assert db.query(UserSession).filter(UserSession.user_id == member_id).count() == 0
    assert db.query(ScanResult).filter(ScanResult.tenant_id == org_tenant).one().scanned_by_user_id is None
    assert not invitation_repo.list_pending_for_email(db, "friend@example.com")
    assert db.query(AuditLog).filter(AuditLog.tenant_id == personal).count() == 0  # their own sign-in rows went
    set_session_tenant(db, org_tenant)  # RLS: the session still points at the deleted personal tenant
    left = db.query(AuditLog).filter(AuditLog.action == "account.deleted").one()
    assert left.actor == "member@example.com" and left.tenant_id == org_tenant  # the org keeps a trace
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_deleting_an_account_also_clears_references_left_in_an_org_they_already_left(client, db):
    owner = _setup_owner(client)
    headers = _register(client)
    member_id = db.query(User).filter(User.email == "member@example.com").one().id
    org = _org_with(db, (owner["user"]["id"], "admin"))  # the member is no longer in it
    invitation_repo.create(db, org_id=org.id, email="friend@example.com", invited_by_user_id=member_id)
    db.add(ScanResult(owner="acme-del", score=90, total_checks=1, failed_checks=0, checks_json="[]", tenant_id=org.tenant_id, scanned_by_user_id=member_id))
    db.commit()

    resp = client.post("/auth/me/delete", headers=headers, json={"confirm_email": "member@example.com", "password": "supersecret1234"})
    assert resp.status_code == 204
    db.expire_all()
    assert db.query(User).filter(User.id == member_id).first() is None
    set_session_tenant(db, org.tenant_id)
    assert db.query(ScanResult).filter(ScanResult.tenant_id == org.tenant_id).one().scanned_by_user_id is None


def test_account_deletion_is_confirmed_and_refused_when_it_would_strand_something(client, db):
    owner = _setup_owner(client)
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    headers = _register(client)
    member_id = db.query(User).filter(User.email == "member@example.com").one().id
    _org_with(db, (member_id, "admin"))  # the only admin of acme-del

    typed_wrong = client.post("/auth/me/delete", headers=headers, json={"confirm_email": "nope@example.com", "password": "supersecret1234"})
    assert typed_wrong.status_code == 400
    wrong_password = client.post("/auth/me/delete", headers=headers, json={"confirm_email": "member@example.com", "password": "wrong-password-1"})
    assert wrong_password.status_code == 400
    sole_admin = client.post("/auth/me/delete", headers=headers, json={"confirm_email": "member@example.com", "password": "supersecret1234"})
    assert sole_admin.status_code == 409 and "acme-del" in sole_admin.json()["detail"]
    workspace_admin = client.post("/auth/me/delete", headers=owner_headers, json={"confirm_email": "owner@example.com", "password": "supersecret1234"})
    assert workspace_admin.status_code == 409 and "workspace admin" in workspace_admin.json()["detail"]
    assert db.query(User).filter(User.id == member_id).first() is not None  # nothing was deleted


def test_a_github_only_account_deletes_with_just_its_email(client, db):
    _setup_owner(client)
    user_id, headers = _github_only_headers(db)
    assert client.post("/auth/me/delete", headers=headers, json={"confirm_email": "gh-only@example.com"}).status_code == 204
    db.expire_all()
    assert db.query(User).filter(User.id == user_id).first() is None
