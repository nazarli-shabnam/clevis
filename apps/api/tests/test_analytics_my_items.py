"""Tests for the My PRs / My Reviews / My Issues paginated list endpoints."""

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
    return _make_user(db, "myitems@example.com")


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


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
def test_requires_auth(db, path):
    a = FastAPI()
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(router)
    resp = TestClient(a).get(f"{path}?owner=acme")
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
def test_no_token_available_returns_400(http, path):
    resp = http.get(f"{path}?owner=acme")
    assert resp.status_code == 400


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
def test_degrades_to_empty_when_login_unresolvable(http, path):
    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.github.com/user"),
            response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user")),
        )
        resp = http.get(f"{path}?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"items": [], "total_count": 0, "page": 1, "per_page": 25, "identity_unresolved": True, "truncated": False}


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
def test_propagates_unexpected_user_endpoint_error(http, path):
    """A non-403 failure calling GET /user (a real auth/server problem, not the expected
    "installation token can't call /user" case) must propagate rather than being silently
    swallowed as identity_unresolved."""
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
            http.get(f"{path}?owner=acme")


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
def test_propagates_network_error_from_user_endpoint(http, path):
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
            http.get(f"{path}?owner=acme")


def _forbidden_user_endpoint(expected_login=None, items=None):
    """GitHubClient.request stand-in: an installation token gets 403 on /user; searches are recorded."""
    def _request(method, path, params=None):
        if path == "/user":
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://api.github.com/user"),
                response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user")),
            )
        if path == "/search/issues":
            if items is not None:
                items.append(params["q"])
            return {"items": [], "total_count": 0}
        return {}
    return _request


def _link_github(db, user: UserOut, *, github_user_id, github_login):
    row = db.get(User, user.id)
    row.github_user_id, row.github_login = github_user_id, github_login
    db.commit()


def test_uses_the_login_github_currently_gives_the_linked_account_not_the_stored_one(db, mock_user, http):
    """The stored github_login is only refreshed at sign-in, so after a rename it can name someone else.
    With an installation token (403 on /user) the current login is looked up by the immutable id (#1245)."""
    _link_github(db, mock_user, github_user_id=4242, github_login="alice")  # stored name is stale
    queries: list[str] = []

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghs_install"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
        patch("src.routers.analytics.github_app.get_user_login_by_id", return_value="alice2") as by_id,
    ):
        mock_client.return_value.request.side_effect = _forbidden_user_endpoint(items=queries)
        resp = http.get("/me/github/my-prs?owner=acme")

    assert resp.status_code == 200
    assert resp.json()["identity_unresolved"] is False
    by_id.assert_called_once_with("ghs_install", 4242)
    assert queries and all("alice2" in q and "author:alice " not in q + " " for q in queries)


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues", "/me/github/my-view"])
def test_identity_unresolved_when_the_linked_account_no_longer_exists(db, mock_user, http, path):
    _link_github(db, mock_user, github_user_id=4242, github_login="alice")
    queries: list[str] = []

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghs_install"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
        patch("src.routers.analytics.github_app.get_user_login_by_id", return_value=None),
    ):
        mock_client.return_value.request.side_effect = _forbidden_user_endpoint(items=queries)
        resp = http.get(f"{path}?owner=acme")

    assert resp.status_code == 200
    assert resp.json()["identity_unresolved"] is True
    assert queries == []  # nothing was searched under a name we could not verify


def test_my_prs_success_returns_pagination_fields(http):
    pr_item = {
        "number": 12,
        "title": "Fix bug",
        "repository_url": "https://api.github.com/repos/acme/api",
        "html_url": "https://github.com/acme/api/pull/12",
        "updated_at": "2026-07-20T00:00:00Z",
    }

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            assert "author:octocat" in params["q"]
            assert params["page"] == 2
            assert params["per_page"] == 10
            return {"items": [pr_item], "total_count": 37}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = http.get("/me/github/my-prs?owner=acme&page=2&per_page=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 37
    assert body["page"] == 2
    assert body["per_page"] == 10
    assert len(body["items"]) == 1
    assert body["items"][0]["repository"] == "acme/api"


def test_my_reviews_uses_review_requested_query(http):
    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            assert "review-requested:octocat" in params["q"]
            return {"items": [], "total_count": 0}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = http.get("/me/github/my-reviews?owner=acme")

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_my_issues_success_maps_issue_summaries(http):
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
        if path == "/search/issues":
            assert "assignee:octocat" in params["q"]
            return {"items": [issue_item], "total_count": 1}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = http.get("/me/github/my-issues?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["repository"] == "acme/worker"


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
@pytest.mark.parametrize("params", ["per_page=101", "per_page=0", "page=0"])
def test_pagination_params_are_validated(http, path, params):
    resp = http.get(f"{path}?owner=acme&{params}")
    assert resp.status_code == 422


def test_a_search_with_more_than_1000_matches_is_marked_truncated(http):
    def request(method, path, params=None):
        return {"login": "octocat"} if path == "/user" else {"items": [], "total_count": 4321}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = request
        body = http.get("/me/github/my-prs?owner=acme").json()
    assert body["total_count"] == 1000 and body["truncated"] is True


def _out_of_window_body(http, total_count):
    calls = []

    def request(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        calls.append(params)
        return {"items": [{"ignored": True}], "total_count": total_count}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = request
        resp = http.get("/me/github/my-prs?owner=acme&page=11&per_page=100")
    assert resp.status_code == 200
    return resp.json(), calls


def test_page_times_per_page_over_1000_asks_github_only_for_the_count(http):
    body, calls = _out_of_window_body(http, 4321)
    # total_count is capped at the reachable max (not 0) so page/lastPage math on the
    # client stays consistent instead of regressing once paging crosses GitHub's window.
    assert body == {"items": [], "total_count": 1000, "page": 11, "per_page": 100, "identity_unresolved": False, "truncated": True}
    assert [(c["page"], c["per_page"]) for c in calls] == [(1, 1)]


def test_an_out_of_window_page_is_not_marked_truncated_when_few_items_match(http):
    body, _ = _out_of_window_body(http, 3)
    assert body["items"] == [] and body["total_count"] == 3 and body["truncated"] is False


def test_search_failure_is_a_github_error_not_an_empty_list(http):
    # A failed search used to answer 200 with no items, which the page showed as "nothing open" (#1083).
    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "https://api.github.com/search/issues"),
                response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/search/issues")),
            )
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = http.get("/me/github/my-prs?owner=acme")

    assert resp.status_code == 400
    assert "403" in resp.json()["detail"] and "items" not in resp.json()


def test_non_dict_search_response_degrades_to_empty(http):
    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            return []
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = http.get("/me/github/my-prs?owner=acme")

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total_count"] == 0


def test_falls_back_to_client_supplied_token_header(http):
    with patch("src.routers.analytics.GitHubClient") as mock_client:
        # 403 = the expected degrade-to-empty /user case; this test targets the
        # X-GitHub-Token header path, not /user's error handling.
        mock_client.return_value.request.side_effect = httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", "https://api.github.com/user"),
            response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/user")),
        )
        resp = http.get("/me/github/my-prs?owner=acme", headers={"X-GitHub-Token": "ghp_client"})

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_my_reviews_asks_github_for_oldest_first_and_maps_created_at(http):
    pr_item = {
        "number": 5,
        "title": "Old PR",
        "repository_url": "https://api.github.com/repos/acme/api",
        "html_url": "https://github.com/acme/api/pull/5",
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-07-20T00:00:00Z",
    }
    seen = {}

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            seen.update(params)
            return {"items": [pr_item], "total_count": 1}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = http.get("/me/github/my-reviews?owner=acme")

    assert resp.status_code == 200
    assert (seen["sort"], seen["order"]) == ("created", "asc")
    assert resp.json()["items"][0]["created_at"].startswith("2026-06-01")


def test_my_prs_keeps_githubs_default_sort(http):
    seen = {}

    def _request_side_effect(method, path, params=None):
        if path == "/user":
            return {"login": "octocat"}
        if path == "/search/issues":
            seen.update(params)
            return {"items": [], "total_count": 0}
        return {}

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request_side_effect
        http.get("/me/github/my-prs?owner=acme")

    assert "sort" not in seen


@pytest.mark.parametrize("path", ["/me/github/my-prs", "/me/github/my-reviews", "/me/github/my-issues"])
@pytest.mark.parametrize(
    "failure,status",
    [
        (
            httpx.HTTPStatusError(
                "rate limited",
                request=httpx.Request("GET", "https://api.github.com/search/issues"),
                response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/search/issues")),
            ),
            400,
        ),
        (httpx.RequestError("boom", request=httpx.Request("GET", "https://api.github.com/search/issues")), 503),
    ],
)
def test_a_failed_search_is_an_error_not_an_empty_list(http, path, failure, status):
    def _request(method, path_, params=None):
        if path_ == "/user":
            return {"login": "octocat"}
        raise failure

    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp_test"),
        patch("src.routers.analytics.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request.side_effect = _request
        resp = http.get(f"{path}?owner=acme")

    assert resp.status_code == status
    assert "items" not in resp.json()
