"""Tests for the connected-orgs (GitHub App installations) list endpoint."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.repositories import installation_repo
from src.routers.installations import router as inst_router

_USER = UserOut(id=1, email="u@e.com", name=None, is_owner=True)
_NON_OWNER = UserOut(id=2, email="member@e.com", name=None, is_owner=False)


@pytest.fixture()
def inst_client(db):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: _USER
    return TestClient(app)


@pytest.fixture()
def inst_client_non_owner(db):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: _NON_OWNER
    return TestClient(app)


def test_list_installations_empty(inst_client):
    resp = inst_client.get("/github/app/installations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_installations_returns_rows(inst_client, db):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42
    )
    resp = inst_client.get("/github/app/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_login"] == "acme"
    assert data[0]["installation_id"] == 42


def test_list_installations_requires_auth(db):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get("/github/app/installations")
    assert resp.status_code == 401


def test_list_installations_non_owner_forbidden(inst_client_non_owner):
    resp = inst_client_non_owner.get("/github/app/installations")
    assert resp.status_code == 403
