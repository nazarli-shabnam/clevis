"""Tests for auth router and config router."""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth, require_workspace_admin
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
    assert body["user"]["is_workspace_admin"] is True
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


# register

def test_register_creates_non_owner(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["is_workspace_admin"] is False
    assert body["user"]["email"] == "member@example.com"


def test_register_before_setup_rejected(auth_client):
    """/auth/setup must run first to create the workspace admin — registering before that
    would leave the instance with no admin, since /setup 409s once any user exists."""
    resp = auth_client.post(
        "/auth/register", json={"email": "first@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


def test_register_rejects_short_password(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post("/auth/register", json={"email": "a@b.com", "password": "tooshort"})
    assert resp.status_code == 422


def test_register_rejects_duplicate_email(auth_client):
    _setup_owner(auth_client, email="dupe@example.com")
    resp = auth_client.post(
        "/auth/register", json={"email": "dupe@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


def test_register_disabled_returns_403(auth_client):
    _setup_owner(auth_client)
    with patch("src.routers.auth.get_config", return_value="false"):
        resp = auth_client.post(
            "/auth/register", json={"email": "blocked@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 403


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
    assert data["is_workspace_admin"] is True


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


# revoke-sessions

def test_revoke_sessions_unauthenticated(auth_client):
    resp = auth_client.post("/auth/me/revoke-sessions")
    assert resp.status_code == 401


def test_revoke_sessions_invalidates_existing_token(auth_client):
    token = _setup_owner(auth_client)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = auth_client.post("/auth/me/revoke-sessions", headers=headers)
    assert resp.status_code == 200

    resp = auth_client.get("/auth/me", headers=headers)
    assert resp.status_code == 401


def test_revoke_sessions_new_login_still_works(auth_client):
    _setup_owner(auth_client)
    auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    )
    first_token = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    ).json()["access_token"]
    auth_client.post(
        "/auth/me/revoke-sessions", headers={"Authorization": f"Bearer {first_token}"}
    )
    new_token = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    ).json()["access_token"]
    resp = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert resp.status_code == 200


# ── Config router ─────────────────────────────────────────────────────────────

_OWNER = UserOut(id=1, email="owner@example.com", name=None, is_workspace_admin=True)
_VIEWER = UserOut(id=2, email="viewer@example.com", name=None, is_workspace_admin=False)

_MOCK_CONFIG = {
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
    a.dependency_overrides[require_workspace_admin] = lambda: _OWNER
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


def test_get_config_unauthenticated(config_client):
    resp = config_client.get("/config")
    assert resp.status_code == 401


def test_get_config_non_owner_forbidden(config_client_viewer):
    resp = config_client_viewer.get("/config")
    assert resp.status_code == 403


def test_get_config_owner(config_client_owner):
    with patch("src.routers.config.read_all", return_value=_MOCK_CONFIG):
        resp = config_client_owner.get("/config")
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


def test_update_config_valid_worker_poll_seconds(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value=_MOCK_CONFIG),
    ):
        resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("worker_poll_seconds", "10")


# github_api_base and cors_origins moved to env vars — no longer runtime-editable.
@pytest.mark.parametrize("key", ["github_api_base", "cors_origins"])
def test_update_config_removed_keys_rejected(config_client_owner, key):
    resp = config_client_owner.put(f"/config/{key}", json={"value": "https://x.com"})
    assert resp.status_code == 400


@pytest.mark.parametrize("value", ["yes", "1", ""])
def test_update_config_invalid_bool(config_client_owner, value):
    resp = config_client_owner.put("/config/registration_enabled", json={"value": value})
    assert resp.status_code == 422


def test_update_config_valid_bool(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value={**_MOCK_CONFIG, "registration_enabled": "false"}),
    ):
        resp = config_client_owner.put("/config/registration_enabled", json={"value": "false"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("registration_enabled", "false")


def test_update_config_success(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value={**_MOCK_CONFIG, "worker_poll_seconds": "10"}),
    ):
        resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("worker_poll_seconds", "10")
    assert resp.json()["worker_poll_seconds"] == "10"
