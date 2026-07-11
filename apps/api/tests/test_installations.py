"""Tests for org-scoped and personal installation endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.routers.installations import router as inst_router

_OUTSIDER = UserOut(id=99999, email="outsider@e.com", name=None, is_workspace_admin=False)


def _make_user(db, email: str, github_login: str | None = None) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, github_login=github_login)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


@pytest.fixture()
def acme_org(db):
    admin = _make_user(db, "admin@e.com")
    member = _make_user(db, "member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def test_list_org_installations_empty(db, acme_org):
    resp = _client(db, acme_org["admin"]).get("/orgs/acme/installations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_org_installations_returns_rows(db, acme_org):
    installation_repo.create(
        db,
        account_login="acme",
        account_type="Organization",
        auth_mode="app",
        installation_id=42,
        org_id=acme_org["org"].id,
    )
    resp = _client(db, acme_org["member"]).get("/orgs/acme/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_login"] == "acme"


def test_list_org_installations_outsider_forbidden(db, acme_org):
    resp = _client(db, _OUTSIDER).get("/orgs/acme/installations")
    assert resp.status_code == 403


def test_list_org_installations_requires_auth(db, acme_org):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get("/orgs/acme/installations")
    assert resp.status_code == 401


def test_sync_org_installation_requires_admin(db, acme_org):
    resp = _client(db, acme_org["member"]).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
    )
    assert resp.status_code == 403


def test_sync_org_installation_admin_ok(db, acme_org):
    resp = _client(db, acme_org["admin"]).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
    )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True


def test_personal_installations_scoped_to_self(db):
    me = _make_user(db, "shabnam@e.com")
    someone_else = _make_user(db, "someoneelse@e.com")
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=1, owner_user_id=me.id
    )
    installation_repo.create(
        db,
        account_login="someoneelse",
        account_type="User",
        auth_mode="app",
        installation_id=2,
        owner_user_id=someone_else.id,
    )
    resp = _client(db, me).get("/me/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_login"] == "shabnam"


def test_sync_personal_installation(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    resp = _client(db, me).post(
        "/me/installations/sync",
        json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
    )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True


def test_sync_personal_installation_requires_linked_github_account(db):
    me = _make_user(db, "unlinked@e.com")
    resp = _client(db, me).post(
        "/me/installations/sync",
        json={"account_login": "someone-else", "account_type": "User", "installation_id": 3},
    )
    assert resp.status_code == 403


def test_sync_personal_installation_login_mismatch_forbidden(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    resp = _client(db, me).post(
        "/me/installations/sync",
        json={"account_login": "someone-else", "account_type": "User", "installation_id": 3},
    )
    assert resp.status_code == 403
