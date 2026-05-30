"""Tests for auth router and config router."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth, require_owner
from src.core.db import get_db
from src.routers.auth import router as auth_router
from src.routers.config import router as config_router


# ── Auth router ───────────────────────────────────────────────────────────────

@pytest.fixture()
def auth_app(db):
    a = FastAPI()
    a.include_router(auth_router, prefix="/auth")
    a.dependency_overrides[get_db] = lambda: db
    return a


@pytest.fixture()
def auth_client(auth_app):
    return TestClient(auth_app)


def _setup_owner(client, email="owner@example.com", password="supersecret1234"):
    """POST /auth/setup and return the response body."""
    resp = client.post("/auth/setup", json={"email": email, "password": password})
    assert resp.status_code == 201
    return resp.json()


# setup-required

def test_setup_required_no_users(auth_client):
    resp = auth_client.get("/auth/setup-required")
    assert resp.status_code == 200
    assert resp.json()["setup_required"] is True


def test_setup_required_with_user(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.get("/auth/setup-required")
    assert resp.status_code == 200
    assert resp.json()["setup_required"] is False


# setup

def test_setup_returns_token_and_owner(auth_client):
    body = _setup_owner(auth_client)
    assert "access_token" in body
    assert body["user"]["is_owner"] is True
    assert body["user"]["email"] == "owner@example.com"


def test_setup_rejects_short_password(auth_client):
    resp = auth_client.post("/auth/setup", json={"email": "a@b.com", "password": "tooshort"})
    assert resp.status_code == 422


def test_setup_rejects_duplicate(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/setup", json={"email": "other@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


# login

def test_login_valid(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "wrongpassword12"}
    )
    assert resp.status_code == 401


def test_login_unknown_email(auth_client):
    resp = auth_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 401


# me

def test_me_unauthenticated(auth_client):
    resp = auth_client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_profile(auth_client):
    token = _setup_owner(auth_client)["access_token"]
    resp = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "owner@example.com"
    assert data["is_owner"] is True


def test_patch_me_unauthenticated(auth_client):
    resp = auth_client.patch("/auth/me", json={"name": "Alice"})
    assert resp.status_code == 401


def test_patch_me_updates_name(auth_client):
    token = _setup_owner(auth_client)["access_token"]
    resp = auth_client.patch(
        "/auth/me",
        json={"name": "Alice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alice"


# ── Config router ─────────────────────────────────────────────────────────────

_OWNER = UserOut(id=1, email="owner@example.com", name=None, is_owner=True)
_VIEWER = UserOut(id=2, email="viewer@example.com", name=None, is_owner=False)

_MOCK_CONFIG = {
    "github_api_base": "https://api.github.com",
    "cors_origins": '["*"]',
    "worker_poll_seconds": "5",
}


@pytest.fixture()
def config_client():
    """No auth override — all protected endpoints return 401."""
    a = FastAPI()
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


@pytest.fixture()
def config_client_viewer():
    """Authenticated as a non-owner user."""
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: _VIEWER
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


@pytest.fixture()
def config_client_owner():
    """Authenticated as the owner."""
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: _OWNER
    a.dependency_overrides[require_owner] = lambda: _OWNER
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


def test_get_config_unauthenticated(config_client):
    resp = config_client.get("/config")
    assert resp.status_code == 401


def test_get_config_authenticated(config_client_viewer):
    with patch("src.routers.config.read_all", return_value=_MOCK_CONFIG):
        resp = config_client_viewer.get("/config")
    assert resp.status_code == 200
    assert resp.json()["worker_poll_seconds"] == "5"


def test_update_config_unauthenticated(config_client):
    resp = config_client.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 401


def test_update_config_non_owner_forbidden(config_client_viewer):
    resp = config_client_viewer.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 403


def test_update_config_unknown_key(config_client_owner):
    resp = config_client_owner.put("/config/unknown_key", json={"value": "x"})
    assert resp.status_code == 400


def test_update_config_invalid_int(config_client_owner):
    resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "notanint"})
    assert resp.status_code == 422


@pytest.mark.parametrize("value", ["0", "-5"])
def test_update_config_int_below_minimum(config_client_owner, value):
    resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": value})
    assert resp.status_code == 422


@pytest.mark.parametrize("value", ["", "  ", "api.github.com", "ftp://x"])
def test_update_config_invalid_github_api_base(config_client_owner, value):
    resp = config_client_owner.put("/config/github_api_base", json={"value": value})
    assert resp.status_code == 422


def test_update_config_valid_github_api_base(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value=_MOCK_CONFIG),
    ):
        resp = config_client_owner.put(
            "/config/github_api_base", json={"value": "https://ghe.example.com/api/v3"}
        )
    assert resp.status_code == 200
    mock_set.assert_called_once_with("github_api_base", "https://ghe.example.com/api/v3")


def test_update_config_invalid_json_array(config_client_owner):
    resp = config_client_owner.put("/config/cors_origins", json={"value": "not-json"})
    assert resp.status_code == 422


def test_update_config_success(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value={**_MOCK_CONFIG, "worker_poll_seconds": "10"}),
    ):
        resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("worker_poll_seconds", "10")
    assert resp.json()["worker_poll_seconds"] == "10"
