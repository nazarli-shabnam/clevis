"""Tests for the org invitation create/list/revoke/accept flow."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.invitations import router as invitations_router


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def acme_org(db):
    admin = _make_user(db, "admin@e.com")
    member = _make_user(db, "member@e.com")
    invitee = _make_user(db, "bob@acme.com")
    wrong_email = _make_user(db, "carol@acme.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member, "invitee": invitee, "wrong_email": wrong_email}


def _client(db, user):
    app = FastAPI()
    app.include_router(invitations_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


def test_create_invitation_requires_admin(db, acme_org):
    resp = _client(db, acme_org["member"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 403


def test_create_invitation_admin_ok(db, acme_org):
    resp = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["invitation"]["email"] == "bob@acme.com"
    assert body["invitation"]["status"] == "pending"
    assert "/invite/" in body["invite_link"]


def test_list_invitations_admin_only(db, acme_org):
    _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    resp = _client(db, acme_org["admin"]).get("/orgs/acme/invitations")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = _client(db, acme_org["member"]).get("/orgs/acme/invitations")
    assert resp.status_code == 403


def test_preview_invitation_unauthenticated(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    app = FastAPI()
    app.include_router(invitations_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get(f"/invitations/{token}")
    assert resp.status_code == 200
    assert resp.json() == {"org_login": "acme", "email": "bob@acme.com", "status": "pending"}


def test_accept_invitation_wrong_email_forbidden(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    resp = _client(db, acme_org["wrong_email"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 403


def test_accept_invitation_ok(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 200
    assert resp.json() == {"org_login": "acme", "role": "member"}

    # Accepting again fails — invitation is no longer pending.
    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 409


def test_revoke_invitation(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    invitation_id = created["invitation"]["id"]

    resp = _client(db, acme_org["admin"]).post(f"/orgs/acme/invitations/{invitation_id}/revoke")
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    token = created["invite_link"].rsplit("/", 1)[-1]
    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 409
