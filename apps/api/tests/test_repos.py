"""Tests for the repos router (Phase 8 groundwork)."""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.repos import router as repos_router
from src.routers.repos import _stats_cache

_ADMIN = UserOut(id=1, email="admin@example.com", name=None, is_workspace_admin=False)


@pytest.fixture(autouse=True)
def _clear_stats_cache():
    _stats_cache.clear()
    yield
    _stats_cache.clear()


@pytest.fixture()
def acme_org(db):
    user = User(id=_ADMIN.id, email=_ADMIN.email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member")
    return org


@pytest.fixture()
def repos_client(db, acme_org):
    app = FastAPI()
    app.include_router(repos_router)
    app.dependency_overrides[require_auth] = lambda: _ADMIN
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_list_repos_returns_paginated_results(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {
                "name": "demo",
                "full_name": "acme/demo",
                "private": False,
                "language": "Python",
                "stargazers_count": 3,
                "open_issues_count": 1,
                "pushed_at": "2026-07-01T00:00:00Z",
                "default_branch": "main",
                "html_url": "https://github.com/acme/demo",
            }
        ]
        resp = repos_client.post("/orgs/acme/repos", json={"token": "ghp_testtoken123456789012345678901234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["repos"][0]["name"] == "demo"
    mock_client.return_value.request_paginated.assert_called_once_with(
        "/orgs/acme/repos", params={"type": "all", "sort": "pushed"}
    )


def test_list_repos_no_installation_and_no_token_returns_400(repos_client):
    resp = repos_client.post("/orgs/acme/repos", json={})
    assert resp.status_code == 400


def test_list_repos_maps_github_status_error_to_400(repos_client):
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x"))
    error = httpx.HTTPStatusError("missing", request=response.request, response=response)
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = error
        resp = repos_client.post("/orgs/acme/repos", json={"token": "ghp_testtoken123456789012345678901234"})
    assert resp.status_code == 400
    assert "404" in resp.json()["detail"]


def test_repo_stats_treats_202_empty_body_as_not_ready(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [{}, {}, {}]
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit_activity"] == []
    assert body["participation"] == {}
    assert body["contributors"] == []


def test_repo_stats_second_call_is_served_from_cache(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [
            [{"week": 1, "total": 5}],
            {"all": [1], "owner": [1]},
            [{"login": "octocat", "total": 5}],
        ]
        first = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
        second = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert mock_client.return_value.request.call_count == 3


def test_list_repos_maps_github_request_error_to_503(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = httpx.RequestError("boom")
        resp = repos_client.post("/orgs/acme/repos", json={"token": "ghp_testtoken123456789012345678901234"})
    assert resp.status_code == 503


def test_repo_stats_no_installation_and_no_token_returns_400(repos_client):
    resp = repos_client.post("/orgs/acme/repos/acme/demo/stats", json={})
    assert resp.status_code == 400


def test_repo_stats_maps_github_status_error_to_400(repos_client):
    response = httpx.Response(500, request=httpx.Request("GET", "https://api.github.com/x"))
    error = httpx.HTTPStatusError("boom", request=response.request, response=response)
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = error
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 400


def test_list_pulls_maps_github_status_error_to_400(repos_client):
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x"))
    error = httpx.HTTPStatusError("missing", request=response.request, response=response)
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = error
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/pulls", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 400


def test_list_pulls_no_installation_and_no_token_returns_400(repos_client):
    resp = repos_client.post("/orgs/acme/repos/acme/demo/pulls", json={})
    assert resp.status_code == 400


def test_repo_stats_owner_mismatch_returns_403(repos_client):
    resp = repos_client.post(
        "/orgs/acme/repos/someone-else/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
    )
    assert resp.status_code == 403


def test_list_pulls_returns_summaries(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {
                "number": 7,
                "title": "Add feature",
                "user": {"login": "octocat"},
                "created_at": "2026-07-01T00:00:00Z",
                "html_url": "https://github.com/acme/demo/pull/7",
            }
        ]
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/pulls", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["pulls"][0]["user"] == "octocat"
    mock_client.return_value.request_paginated.assert_called_once_with(
        "/repos/acme/demo/pulls", params={"state": "open"}
    )


def test_non_member_forbidden(db):
    org_repo.get_or_create(db, github_login="acme")
    app = FastAPI()
    app.include_router(repos_router)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=99, email="outsider@example.com", name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    resp = client.post("/orgs/acme/repos", json={})
    assert resp.status_code == 403
