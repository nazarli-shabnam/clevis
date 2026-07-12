"""Tests for actions-cache GitHub error mapping."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import installation_repo
from src.routers.actions_cache import router as cache_router

_USER = UserOut(id=1, email="u@example.com", name=None, is_workspace_admin=False)


@pytest.fixture()
def cache_client(db):
    app = FastAPI()
    app.include_router(cache_router)
    app.dependency_overrides[require_auth] = lambda: _USER
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_list_caches_maps_github_status_error_to_400(cache_client):
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x"))
    error = httpx.HTTPStatusError("missing", request=response.request, response=response)
    with patch("src.routers.actions_cache.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = error
        resp = cache_client.post(
            "/me/repos/acme/demo/actions-caches",
            json={"token": "ghp_testtoken123456789012345678901234"},
        )
    assert resp.status_code == 400
    assert "404" in resp.json()["detail"]


# ── GitHub App installation-token fallback ─────────────────────────────────────

def test_list_caches_uses_installation_token_when_no_client_token(cache_client, db, monkeypatch):
    from pydantic import SecretStr

    from src.core.config import settings

    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_app_private_key", SecretStr("dummy-pem"))
    db.add(User(id=_USER.id, email=_USER.email, name=None, password_hash=None, is_workspace_admin=False))
    db.commit()
    installation_repo.create(
        db, account_login="acme", account_type="User", auth_mode="app", installation_id=42, owner_user_id=_USER.id
    )
    with (
        patch("src.routers.actions_cache.GitHubClient") as mock_client,
        patch("src.services.token_resolution.github_app.get_installation_token", return_value="minted-token"),
    ):
        mock_client.return_value.request.return_value = {"total_count": 0, "actions_caches": []}
        resp = cache_client.post("/me/repos/acme/demo/actions-caches", json={})
    assert resp.status_code == 200
    mock_client.assert_called_once_with("minted-token")


def test_list_caches_no_installation_and_no_token_returns_400(cache_client):
    resp = cache_client.post("/me/repos/acme/demo/actions-caches", json={})
    assert resp.status_code == 400


def test_clear_caches_dry_run_does_not_require_a_token(cache_client):
    resp = cache_client.post(
        "/me/repos/acme/demo/actions-caches/clear",
        json={"dry_run": True},
    )
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True
