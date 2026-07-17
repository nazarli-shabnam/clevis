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
                "description": "A demo repository",
                "language": "Python",
                "stargazers_count": 3,
                "forks_count": 1,
                "watchers_count": 3,
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


_REPO_META = {
    "stargazers_count": 24,
    "forks_count": 3,
    "watchers_count": 24,
    "open_issues_count": 12,
    "default_branch": "main",
}


def _not_found() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/x")
    return httpx.HTTPStatusError("missing", request=request, response=httpx.Response(404, request=request))


def test_repo_stats_treats_202_empty_body_as_not_ready(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [_REPO_META, {}, {}, {}, _not_found()]
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["commit_activity"] == []
    assert body["participation"] == {}
    assert body["contributors"] == []


def test_repo_stats_includes_repo_metadata_and_latest_release(repos_client):
    release = {
        "tag_name": "v0.4.1",
        "published_at": "2026-07-15T00:00:00Z",
        "html_url": "https://github.com/acme/demo/releases/tag/v0.4.1",
    }
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [_REPO_META, [], {}, [], release]
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stargazers_count"] == 24
    assert body["forks_count"] == 3
    assert body["watchers_count"] == 24
    assert body["open_issues_count"] == 12
    assert body["default_branch"] == "main"
    assert body["latest_release"] == release


def test_repo_stats_latest_release_is_null_when_repo_has_no_releases(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [_REPO_META, [], {}, [], _not_found()]
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/stats", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    assert resp.json()["latest_release"] is None


def test_repo_stats_second_call_is_served_from_cache(repos_client):
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [
            _REPO_META,
            [{"week": 1, "total": 5}],
            {"all": [1], "owner": [1]},
            [{"login": "octocat", "total": 5}],
            _not_found(),
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
    assert mock_client.return_value.request.call_count == 5


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


def test_repo_security_returns_protected_and_enabled(repos_client):
    with (
        patch("src.routers.repos.GitHubClient") as mock_client,
        patch(
            "checks.github_checks.BranchProtectionEnabled.run",
            return_value={"status": "pass", "value": {"checked": 1, "protected": 1, "unknown": 0}},
        ),
        patch(
            "checks.github_checks.SecretScanningEnabled.run",
            return_value={"status": "pass", "value": {"enabled": 1, "total": 1}},
        ),
    ):
        mock_client.return_value.request.return_value = {"name": "demo", "default_branch": "main"}
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/security", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch_protection"] == "protected"
    assert body["secret_scanning"] == "enabled"


def test_repo_security_returns_unprotected_and_disabled(repos_client):
    with (
        patch("src.routers.repos.GitHubClient") as mock_client,
        patch(
            "checks.github_checks.BranchProtectionEnabled.run",
            return_value={"status": "fail", "value": {"checked": 1, "protected": 0, "unknown": 0}},
        ),
        patch(
            "checks.github_checks.SecretScanningEnabled.run",
            return_value={"status": "fail", "value": {"enabled": 0, "total": 1}},
        ),
    ):
        mock_client.return_value.request.return_value = {"name": "demo", "default_branch": "main"}
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/security", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["branch_protection"] == "unprotected"
    assert body["secret_scanning"] == "disabled"


def test_repo_security_returns_unknown_when_branch_check_inconclusive(repos_client):
    # e.g. the token lacks permission to read branch protection (403/429 inside the check).
    with (
        patch("src.routers.repos.GitHubClient") as mock_client,
        patch(
            "checks.github_checks.BranchProtectionEnabled.run",
            return_value={"status": "error", "value": {"checked": 1, "protected": 0, "unknown": 1}},
        ),
        patch(
            "checks.github_checks.SecretScanningEnabled.run",
            return_value={"status": "pass", "value": {"enabled": 1, "total": 1}},
        ),
    ):
        mock_client.return_value.request.return_value = {"name": "demo", "default_branch": "main"}
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/security", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 200
    assert resp.json()["branch_protection"] == "unknown"


def test_repo_security_no_installation_and_no_token_returns_400(repos_client):
    resp = repos_client.post("/orgs/acme/repos/acme/demo/security", json={})
    assert resp.status_code == 400


def test_repo_security_owner_mismatch_returns_403(repos_client):
    resp = repos_client.post(
        "/orgs/acme/repos/someone-else/demo/security", json={"token": "ghp_testtoken123456789012345678901234"}
    )
    assert resp.status_code == 403


def test_repo_security_maps_github_status_error_to_400(repos_client):
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x"))
    error = httpx.HTTPStatusError("missing", request=response.request, response=response)
    with patch("src.routers.repos.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = error
        resp = repos_client.post(
            "/orgs/acme/repos/acme/demo/security", json={"token": "ghp_testtoken123456789012345678901234"}
        )
    assert resp.status_code == 400


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
