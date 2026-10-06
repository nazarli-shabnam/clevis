"""Tests for per-repo delivery-flow metrics (service + endpoint)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.repos import _flow_cache
from src.routers.repos import router as repos_router
from src.services import flow_metrics

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _ago(**kw) -> str:
    return _iso(NOW - timedelta(**kw))


class FakeClient:
    """Routes GitHubClient.request by path; records calls."""

    def __init__(self, pulls=None, reviews=None, runs=None):
        self.pulls = pulls or []
        self.reviews = reviews or {}
        self.runs = runs or []
        self.calls: list[str] = []

    def request(self, method, path, params=None):
        self.calls.append(path)
        if path.endswith("/actions/runs"):
            return {"workflow_runs": self.runs if (params or {}).get("page", 1) == 1 else []}
        if path.endswith("/reviews"):
            number = int(path.split("/")[-2])
            return self.reviews.get(number, [])
        if path.endswith("/pulls"):
            return self.pulls if (params or {}).get("page", 1) == 1 else []
        raise AssertionError(path)


def _pr(number, created_hours_ago, merged_hours_ago, author="dev"):
    return {
        "number": number,
        "user": {"login": author},
        "created_at": _ago(hours=created_hours_ago),
        "merged_at": _ago(hours=merged_hours_ago) if merged_hours_ago is not None else None,
        "updated_at": _ago(hours=merged_hours_ago or 1),
    }


def _run(rid, name, sha, conclusion, started_hours_ago, duration_s):
    start = NOW - timedelta(hours=started_hours_ago)
    return {
        "id": rid,
        "name": name,
        "head_sha": sha,
        "conclusion": conclusion,
        "run_started_at": _iso(start),
        "updated_at": _iso(start + timedelta(seconds=duration_s)),
    }


def test_pr_cycle_time_and_first_review_ignore_author_and_unmerged():
    client = FakeClient(
        pulls=[
            _pr(1, created_hours_ago=48, merged_hours_ago=24),  # 24h cycle
            _pr(2, created_hours_ago=30, merged_hours_ago=10),  # 20h cycle
            _pr(3, created_hours_ago=5, merged_hours_ago=None),  # closed unmerged -> excluded
        ],
        reviews={
            1: [
                {"user": {"login": "dev"}, "submitted_at": _ago(hours=47)},  # author's own comment ignored
                {"user": {"login": "rev"}, "submitted_at": _ago(hours=44)},  # 4h after creation
            ],
            2: [],  # merged with no review
        },
    )
    out, truncated = flow_metrics.pr_metrics(client, "acme", "api", NOW - timedelta(days=30))

    assert truncated is False
    assert out["merged_count"] == 2
    assert out["median_cycle_hours"] == 22.0
    assert out["median_first_review_hours"] == 4.0
    assert out["review_sample_size"] == 2
    assert out["merged_without_review"] == 1
    assert out["review_lookup_failed"] == 0


def test_pr_metrics_excludes_prs_merged_before_the_window():
    client = FakeClient(pulls=[_pr(1, created_hours_ago=24 * 60, merged_hours_ago=24 * 45)])
    out, _ = flow_metrics.pr_metrics(client, "acme", "api", NOW - timedelta(days=30))
    assert out["merged_count"] == 0
    assert out["median_cycle_hours"] is None
    assert out["median_first_review_hours"] is None


def test_review_lookup_failure_is_unknown_not_merged_without_review():
    class Boom(FakeClient):
        def request(self, method, path, params=None):
            if path.endswith("/reviews") and path.split("/")[-2] == "1":
                raise httpx.RequestError("down")
            return super().request(method, path, params)

    client = Boom(pulls=[_pr(1, 10, 5), _pr(2, 10, 5)])  # PR 1: lookup fails; PR 2: genuinely no reviews
    out, _ = flow_metrics.pr_metrics(client, "acme", "api", NOW - timedelta(days=30))
    assert out["merged_count"] == 2
    assert out["median_first_review_hours"] is None
    assert out["review_lookup_failed"] == 1
    assert out["merged_without_review"] == 1  # only the PR whose reviews were fetched and empty


def test_pr_pages_flag_truncation_when_the_cap_cuts_off_in_window_prs():
    full_page = [_pr(i, 10, 5) for i in range(100)]

    class Always(FakeClient):
        def request(self, method, path, params=None):
            return full_page if path.endswith("/pulls") else []

    out, truncated = flow_metrics.pr_metrics(Always(), "acme", "api", NOW - timedelta(days=30))
    assert truncated is True
    assert out["merged_count"] == 300  # 3 pages


def test_pr_pages_are_not_truncated_when_the_window_ends_before_the_cap():
    old = [_pr(i, 24 * 50, 24 * 45) for i in range(100)]  # a full page, but all older than the window

    class Old(FakeClient):
        def request(self, method, path, params=None):
            return old if path.endswith("/pulls") else []

    _, truncated = flow_metrics.pr_metrics(Old(), "acme", "api", NOW - timedelta(days=30))
    assert truncated is False


def test_workflow_duration_failure_rate_and_flaky_commits():
    runs = [
        _run(1, "CI", "aaa", "failure", 10, 120),
        _run(2, "CI", "aaa", "success", 9, 100),  # same commit failed then passed -> flaky
        _run(3, "CI", "bbb", "success", 8, 80),
        _run(4, "CI", "ccc", "failure", 7, 100),  # fails consistently: not flaky
        _run(5, "Lint", "aaa", "cancelled", 6, 10),  # cancelled: neither pass nor fail
    ]
    workflows, truncated = flow_metrics.workflow_metrics(FakeClient(runs=runs), "acme", "api", NOW - timedelta(days=30))
    by_name = {w["name"]: w for w in workflows}

    ci = by_name["CI"]
    assert ci["runs"] == 4
    assert ci["failure_rate"] == 0.5
    assert ci["avg_duration_seconds"] == 100
    assert ci["flaky_commits"] == 1
    assert by_name["Lint"]["failure_rate"] is None
    assert by_name["Lint"]["flaky_commits"] == 0
    assert truncated is False


def test_rerun_that_passed_counts_as_flaky_even_though_the_failed_attempt_is_hidden():
    rerun = {**_run(1, "CI", "aaa", "success", 5, 60), "run_attempt": 2}  # API shows only the latest attempt
    first_try = _run(2, "CI", "bbb", "success", 4, 60)  # attempt 1 passing is not flaky
    workflows, _ = flow_metrics.workflow_metrics(FakeClient(runs=[rerun, first_try]), "acme", "api", NOW - timedelta(days=30))
    assert workflows[0]["flaky_commits"] == 1


def test_workflow_runs_flag_truncation_when_page_cap_is_hit():
    full_page = [_run(i, "CI", f"s{i}", "success", 1, 10) for i in range(100)]

    class Always(FakeClient):
        def request(self, method, path, params=None):
            return {"workflow_runs": full_page}

    _, truncated = flow_metrics.workflow_metrics(Always(), "acme", "api", NOW - timedelta(days=30))
    assert truncated is True


# --- endpoint ---

_ADMIN = UserOut(id=1, email="flow-admin@example.com", name=None, is_workspace_admin=False)


@pytest.fixture(autouse=True)
def _clear_cache():
    _flow_cache.clear()
    yield
    _flow_cache.clear()


@pytest.fixture()
def flow_client(db):
    user = User(id=_ADMIN.id, email=_ADMIN.email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member")
    app = FastAPI()
    app.include_router(repos_router)
    app.dependency_overrides[require_auth] = lambda: _ADMIN
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


_BODY = {"token": "ghp_testtoken123456789012345678901234"}
_PAYLOAD = {
    "repository": "acme/api",
    "window_days": 30,
    "prs": {
        "merged_count": 0, "median_cycle_hours": None, "median_first_review_hours": None,
        "review_sample_size": 0, "merged_without_review": 0,
    },
    "workflows": [],
    "workflows_truncated": False,
    "prs_truncated": False,
}


def test_endpoint_returns_metrics_and_caches_per_token(flow_client):
    with patch("src.routers.repos.flow_metrics.compute", return_value=_PAYLOAD) as compute:
        first = flow_client.post("/orgs/acme/repos/acme/api/flow-metrics", json=_BODY)
        second = flow_client.post("/orgs/acme/repos/acme/api/flow-metrics", json=_BODY)

    assert first.status_code == 200
    assert first.json()["window_days"] == 30
    assert second.json() == first.json()
    assert compute.call_count == 1  # second hit served from cache


def test_endpoint_sweeps_expired_cache_entries(flow_client):
    stale_key = (999, "old", "repo", "tokenhash")
    _flow_cache[stale_key] = (-10_000.0, _PAYLOAD)  # monotonic timestamp far in the past
    with patch("src.routers.repos.flow_metrics.compute", return_value=_PAYLOAD):
        assert flow_client.post("/orgs/acme/repos/acme/api/flow-metrics", json=_BODY).status_code == 200
    assert stale_key not in _flow_cache


def test_endpoint_maps_github_errors(flow_client):
    err = httpx.HTTPStatusError(
        "nope",
        request=httpx.Request("GET", "https://api.github.com/x"),
        response=httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x")),
    )
    with patch("src.routers.repos.flow_metrics.compute", side_effect=err):
        resp = flow_client.post("/orgs/acme/repos/acme/api/flow-metrics", json=_BODY)
    assert resp.status_code >= 400
    assert _flow_cache == {}


def test_endpoint_rejects_non_member(db):
    outsider = UserOut(id=424242, email="outsider-flow@example.com", name=None, is_workspace_admin=False)
    org_repo.get_or_create(db, github_login="acme")
    app = FastAPI()
    app.include_router(repos_router)
    app.dependency_overrides[require_auth] = lambda: outsider
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).post("/orgs/acme/repos/acme/api/flow-metrics", json=_BODY)
    assert resp.status_code in (403, 404)
