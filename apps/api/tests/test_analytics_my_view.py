"""Tests for the My View endpoint."""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.routers.analytics import router


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def mock_user(db):
    return _make_user(db, "myview@example.com")


@pytest.fixture()
def app(db, mock_user):
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: mock_user
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(router)
    return a


@pytest.fixture()
def http(app):
    return TestClient(app)


def test_my_view_requires_auth(db):
    a = FastAPI()
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(router)
    resp = TestClient(a).get("/me/github/my-view?owner=acme")
    assert resp.status_code == 401


def test_my_view_no_token_available_returns_400(http):
    resp = http.get("/me/github/my-view?owner=acme")
    assert resp.status_code == 400


def test_my_view_degrades_to_empty_when_login_unresolvable(http):
    """An installation (App) token can't call GET /user, and this mock_user has no
    GitHub-OAuth-linked login to fall back on either -- this should degrade to an empty
    MyViewResponse flagged identity_unresolved, not 500 the page."""
    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.github.com/user"),
            response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user")),
        )
        resp = http.get("/me/github/my-view?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "my_open_prs": [],
        "review_requests": [],
        "assigned_issues": [],
        "my_recent_runs": [],
        "identity_unresolved": True,
    }


def test_my_view_propagates_unexpected_user_endpoint_error(http):
    """A non-403 failure calling GET /user must propagate, not masquerade as
    identity_unresolved ("this user has zero PRs/issues")."""
    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.github.com/user"),
            response=httpx.Response(500, request=httpx.Request("GET", "https://api.github.com/user")),
        )
        with pytest.raises(httpx.HTTPStatusError):
            http.get("/me/github/my-view?owner=acme")


def test_my_view_propagates_network_error_from_user_endpoint(http):
    """A network-level failure (timeout, DNS, connection reset) calling GET /user must
    also propagate rather than being treated as the expected App-token case."""
    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = httpx.RequestError(
            "boom", request=httpx.Request("GET", "https://api.github.com/user")
        )
        with pytest.raises(httpx.RequestError):
            http.get("/me/github/my-view?owner=acme")


def test_my_view_falls_back_to_users_github_login_when_user_endpoint_unresolvable(app, http):
    """A GitHub App installation token can't call GET /user, but if the signed-in Clevis
    user linked their own GitHub identity via OAuth, my-view should use that login rather
    than degrading to empty."""
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=1, email="myview@example.com", name=None, is_workspace_admin=False, github_login="octocat"
    )

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://api.github.com/user"),
                response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user")),
            )
        if path == "/search/issues":
            q = params["q"]
            if "author:octocat" in q:
                return {"items": [{
                    "number": 12,
                    "title": "Fix bug",
                    "repository_url": "https://api.github.com/repos/acme/api",
                    "html_url": "https://github.com/acme/api/pull/12",
                    "updated_at": "2026-07-20T00:00:00Z",
                }]}
            return {"items": []}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        mock_client.return_value.request_paginated.return_value = []
        resp = http.get("/me/github/my-view?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert body["identity_unresolved"] is False
    assert len(body["my_open_prs"]) == 1
    assert body["my_open_prs"][0]["repository"] == "acme/api"


def test_my_view_success(http):
    pr_item = {
        "number": 12,
        "title": "Fix bug",
        "repository_url": "https://api.github.com/repos/acme/api",
        "html_url": "https://github.com/acme/api/pull/12",
        "updated_at": "2026-07-20T00:00:00Z",
    }
    issue_item = {
        "number": 3,
        "title": "Investigate flake",
        "repository_url": "https://api.github.com/repos/acme/worker",
        "html_url": "https://github.com/acme/worker/issues/3",
        "updated_at": "2026-07-19T00:00:00Z",
    }

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/orgs/acme/repos":
            return {"total_count": 0}
        if path == "/search/issues":
            q = params["q"]
            if "author:octocat" in q:
                return {"items": [pr_item]}
            if "review-requested:octocat" in q:
                return {"items": []}
            if "assignee:octocat" in q:
                return {"items": [issue_item]}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        mock_client.return_value.request_paginated.return_value = []
        resp = http.get("/me/github/my-view?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["my_open_prs"]) == 1
    assert body["my_open_prs"][0]["repository"] == "acme/api"
    assert body["review_requests"] == []
    assert len(body["assigned_issues"]) == 1
    assert body["assigned_issues"][0]["repository"] == "acme/worker"
    assert body["my_recent_runs"] == []


def test_my_view_falls_back_to_client_supplied_token_header(http):
    with patch("src.routers.analytics.GitHubClient") as mock_client:
        # 403 = the expected degrade-to-empty /user case; this test targets the
        # X-GitHub-Token header path, not /user's error handling.
        mock_client.return_value.request.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.github.com/user"),
            response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user")),
        )
        resp = http.get("/me/github/my-view?owner=acme", headers={"X-GitHub-Token": "ghp_client"})

    assert resp.status_code == 200
    assert resp.json()["my_open_prs"] == []


def test_my_view_repos_fetch_failure_degrades_to_empty_recent_runs(http):
    """If the repo-list call itself fails, my-view should still return 200 with an
    empty my_recent_runs rather than propagating the error (repos are only used for
    the recent-runs fan-out here, not required for the search-based PR/issue lists)."""

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            return {"items": []}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        mock_client.return_value.request_paginated.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.github.com/orgs/acme/repos"),
            response=httpx.Response(500, request=httpx.Request("GET", "https://api.github.com/orgs/acme/repos")),
        )
        resp = http.get("/me/github/my-view?owner=acme")

    assert resp.status_code == 200
    assert resp.json()["my_recent_runs"] == []


def test_my_view_resolves_account_type_for_repo_listing(http):
    """A User-account owner must not hit /orgs/{owner}/repos for the recent-runs repo
    fan-out (that endpoint 404s for User accounts) -- my-view should resolve account_type
    the same way personal_analytics_cockpit does and route through the User-account path."""

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/orgs/octocat/repos":
            raise AssertionError("must not call the org repos endpoint for a User account")
        if path == "/installation/repositories":
            return {"repositories": [{"name": "demo"}]}
        if path == "/search/issues":
            return {"items": []}
        if path == "/repos/octocat/demo/actions/runs":
            return {"workflow_runs": []}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.get_account_type", return_value="User"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        mock_client.return_value.request_paginated.side_effect = (
            lambda path, params=None, items_key=None: _request_side_effect("GET", path, params).get(
                items_key or "items", []
            )
        )
        resp = http.get("/me/github/my-view?owner=octocat")

    assert resp.status_code == 200
    assert resp.json()["my_recent_runs"] == []


def test_my_view_search_failure_degrades_each_list_to_empty_but_still_returns_recent_runs(http):
    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://api.github.com/search/issues"),
                response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/search/issues")),
            )
        if path == "/repos/acme/demo/actions/runs":
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.com/acme/demo/actions/runs/1",
                        "created_at": "2026-07-20T00:00:00Z",
                    }
                ]
            }
        if path == "/repos/acme/bad/actions/runs":
            raise httpx.RequestError("boom")
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        mock_client.return_value.request_paginated.return_value = [{"name": "demo"}, {"name": "bad"}]
        resp = http.get("/me/github/my-view?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert body["my_open_prs"] == []
    assert body["review_requests"] == []
    assert body["assigned_issues"] == []
    assert len(body["my_recent_runs"]) == 1
    assert body["my_recent_runs"][0]["repository"] == "acme/demo"
