"""Tests for actions-cache GitHub error mapping."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.routers.actions_cache import router as cache_router

_USER = UserOut(id=1, email="u@example.com", name=None, is_workspace_admin=False)


@pytest.fixture()
def cache_client():
    app = FastAPI()
    app.include_router(cache_router)
    app.dependency_overrides[require_auth] = lambda: _USER
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
