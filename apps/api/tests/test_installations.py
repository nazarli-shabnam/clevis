"""Tests for org-scoped and personal installation endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.routers.installations import router as inst_router

_ADMIN = UserOut(id=1, email="admin@e.com", name=None, is_workspace_admin=False)
_MEMBER = UserOut(id=2, email="member@e.com", name=None, is_workspace_admin=False)
_OUTSIDER = UserOut(id=3, email="outsider@e.com", name=None, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


@pytest.fixture()
def acme_org(db):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=_ADMIN.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=_MEMBER.id, role="member")
    return org


def test_list_org_installations_empty(db, acme_org):
    resp = _client(db, _ADMIN).get("/orgs/acme/installations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_org_installations_returns_rows(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org.id
    )
    resp = _client(db, _MEMBER).get("/orgs/acme/installations")
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
    resp = _client(db, _MEMBER).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
    )
    assert resp.status_code == 403


def test_sync_org_installation_admin_ok(db, acme_org):
    resp = _client(db, _ADMIN).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
    )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True


def test_personal_installations_scoped_to_self(db):
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=1, owner_user_id=_MEMBER.id
    )
    installation_repo.create(
        db, account_login="someoneelse", account_type="User", auth_mode="app", installation_id=2, owner_user_id=_OUTSIDER.id
    )
    resp = _client(db, _MEMBER).get("/me/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_login"] == "shabnam"


def test_sync_personal_installation(db):
    resp = _client(db, _MEMBER).post(
        "/me/installations/sync",
        json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
    )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True
