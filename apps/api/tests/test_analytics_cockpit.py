"""Tests for the Overview cockpit aggregate endpoint (docs/plan.md Phase 12)."""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import Job, User, get_db
from src.repositories import scan_results_repo
from src.routers.analytics import router

_HTTP_ERROR = httpx.HTTPStatusError(
    "boom",
    request=httpx.Request("GET", "https://api.github.com/x"),
    response=httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x")),
)


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def mock_user(db):
    return _make_user(db, "cockpit@example.com")


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


_DEFAULT_SAFE_MOCKS = {
    "src.routers.analytics._safe_list_repos": {"return_value": [{"name": "api"}, {"name": "worker"}]},
    "src.routers.analytics._safe_member_count": {"return_value": 12},
    "src.routers.analytics._safe_recent_events": {"return_value": []},
    "src.routers.analytics._safe_open_pr_count": {"return_value": 7},
    "src.routers.analytics._safe_pr_merge_rate_4w": {"return_value": []},
    "src.routers.analytics._safe_commit_activity_4w": {"return_value": [1, 2, 3, 4]},
    "src.routers.analytics._safe_total_cache_bytes": {"return_value": 123456},
    "src.routers.analytics._safe_commit_heatmap_52w": {"return_value": [0] * 52},
}


def _patch_all(overrides=None):
    mocks = dict(_DEFAULT_SAFE_MOCKS)
    if overrides:
        mocks.update(overrides)
    patchers = [patch(target, **kwargs) for target, kwargs in mocks.items()]
    return patchers


def _start_all(patchers):
    for p in patchers:
        p.start()


def _stop_all(patchers):
    for p in patchers:
        p.stop()


def test_cockpit_requires_auth(db):
    a = FastAPI()
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(router)
    resp = TestClient(a).get("/me/analytics/cockpit/acme")
    assert resp.status_code == 401


def test_cockpit_no_token_available_returns_400(http):
    resp = http.get("/me/analytics/cockpit/acme")
    assert resp.status_code == 400


def test_cockpit_success_all_sources(http, db, mock_user):
    scan_results_repo.insert(db, owner="acme", score=70, total_checks=5, failed_checks=1, checks=[])
    scan_results_repo.insert(db, owner="acme", score=85, total_checks=5, failed_checks=0, checks=[])
    for status in ("done", "done", "done", "failed"):
        job = Job(job_type="github.clear_actions_cache", payload="{}", status=status)
        db.add(job)
    db.commit()

    patchers = _patch_all()
    _start_all(patchers)
    try:
        with patch("src.routers.analytics.resolve_personal_token", return_value="ghp_test"):
            resp = http.get("/me/analytics/cockpit/acme")
    finally:
        _stop_all(patchers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_count"] == 2
    assert body["member_count"] == 12
    assert body["open_pr_count"] == 7
    assert body["commit_activity_4w"] == [1, 2, 3, 4]
    assert body["total_cache_size_bytes"] == 123456
    assert body["latest_score"] == 85
    assert body["score_trend"] == [70, 85]
    assert body["cache_job_success_rate"] == 0.75


def test_cockpit_no_scans_yet(http, db, mock_user):
    patchers = _patch_all()
    _start_all(patchers)
    try:
        with patch("src.routers.analytics.resolve_personal_token", return_value="ghp_test"):
            resp = http.get("/me/analytics/cockpit/acme")
    finally:
        _stop_all(patchers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["latest_score"] is None
    assert body["score_trend"] == []


def test_cockpit_no_cache_jobs_yet(http, db, mock_user):
    patchers = _patch_all()
    _start_all(patchers)
    try:
        with patch("src.routers.analytics.resolve_personal_token", return_value="ghp_test"):
            resp = http.get("/me/analytics/cockpit/acme")
    finally:
        _stop_all(patchers)

    assert resp.status_code == 200
    assert resp.json()["cache_job_success_rate"] == 0.0


def test_cockpit_degrades_when_pr_search_fails(http, db, mock_user):
    patchers = _patch_all({"src.routers.analytics._safe_open_pr_count": {"return_value": 0}})
    _start_all(patchers)
    try:
        with patch("src.routers.analytics.resolve_personal_token", return_value="ghp_test"):
            resp = http.get("/me/analytics/cockpit/acme")
    finally:
        _stop_all(patchers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["open_pr_count"] == 0
    assert body["member_count"] == 12  # other fields unaffected


def test_cockpit_degrades_when_events_fetch_fails(http, db, mock_user):
    patchers = _patch_all({"src.routers.analytics._safe_recent_events": {"return_value": []}})
    _start_all(patchers)
    try:
        with patch("src.routers.analytics.resolve_personal_token", return_value="ghp_test"):
            resp = http.get("/me/analytics/cockpit/acme")
    finally:
        _stop_all(patchers)

    assert resp.status_code == 200
    assert resp.json()["recent_events"] == []


def test_cockpit_fails_when_repo_list_fails(http, db, mock_user):
    patchers = _patch_all({"src.routers.analytics._safe_list_repos": {"side_effect": _HTTP_ERROR}})
    _start_all(patchers)
    try:
        with patch("src.routers.analytics.resolve_personal_token", return_value="ghp_test"):
            resp = http.get("/me/analytics/cockpit/acme")
    finally:
        _stop_all(patchers)

    assert resp.status_code == 400


def test_cockpit_falls_back_to_client_supplied_token_header(http, db, mock_user):
    patchers = _patch_all()
    _start_all(patchers)
    try:
        resp = http.get("/me/analytics/cockpit/acme", headers={"X-GitHub-Token": "ghp_client"})
    finally:
        _stop_all(patchers)

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Unit tests for individual _safe_* helpers' own try/except behavior
# ---------------------------------------------------------------------------


def test_safe_member_count_returns_zero_on_http_error():
    from src.routers.analytics import _safe_member_count

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _HTTP_ERROR
        assert _safe_member_count("acme", "ghp_test") == 0


def test_safe_member_count_returns_zero_on_request_error():
    from src.routers.analytics import _safe_member_count

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = httpx.RequestError("boom")
        assert _safe_member_count("acme", "ghp_test") == 0


def test_safe_open_pr_count_returns_zero_on_http_error():
    from src.routers.analytics import _safe_open_pr_count

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = _HTTP_ERROR
        assert _safe_open_pr_count("acme", "ghp_test") == 0


def test_safe_pr_merge_rate_4w_returns_empty_list_on_error():
    from src.routers.analytics import _safe_pr_merge_rate_4w

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        assert _safe_pr_merge_rate_4w("acme", "ghp_test") == []


def test_safe_pr_merge_rate_4w_returns_four_chronological_buckets():
    from src.routers.analytics import _safe_pr_merge_rate_4w

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.return_value = {"total_count": 3}
        buckets = _safe_pr_merge_rate_4w("acme", "ghp_test")
    assert len(buckets) == 4
    weeks = [b.week for b in buckets]
    assert weeks == sorted(weeks)
    assert all(b.opened == 3 and b.merged == 3 for b in buckets)


def test_safe_commit_activity_4w_returns_zeros_on_error():
    from src.routers.analytics import _safe_commit_activity_4w

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        assert _safe_commit_activity_4w("acme", "ghp_test", ["repo-a"]) == [0, 0, 0, 0]


def test_safe_commit_activity_4w_sums_last_four_weeks_across_repos():
    from src.routers.analytics import _safe_commit_activity_4w

    weeks_a = [{"total": i} for i in range(52)]  # totals 0..51, last 4 are 48,49,50,51
    weeks_b = [{"total": 1} for _ in range(52)]
    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [weeks_a, weeks_b]
        totals = _safe_commit_activity_4w("acme", "ghp_test", ["repo-a", "repo-b"])
    assert totals == [49, 50, 51, 52]


def test_safe_commit_heatmap_52w_returns_zeros_on_error():
    from src.routers.analytics import _safe_commit_heatmap_52w

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        assert _safe_commit_heatmap_52w("acme", "ghp_test", ["repo-a"]) == [0] * 52


def test_safe_commit_heatmap_52w_sums_across_repos():
    from src.routers.analytics import _safe_commit_heatmap_52w

    weeks_a = [{"total": 1} for _ in range(52)]
    weeks_b = [{"total": 2} for _ in range(52)]
    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [weeks_a, weeks_b]
        totals = _safe_commit_heatmap_52w("acme", "ghp_test", ["repo-a", "repo-b"])
    assert len(totals) == 52
    assert all(t == 3 for t in totals)


def test_safe_total_cache_bytes_returns_zero_on_error():
    from src.routers.analytics import _safe_total_cache_bytes

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        assert _safe_total_cache_bytes("acme", "ghp_test", ["repo-a"]) == 0


def test_safe_total_cache_bytes_sums_across_repos():
    from src.routers.analytics import _safe_total_cache_bytes

    with patch("src.routers.analytics.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = [
            {"actions_caches": [{"size_in_bytes": 100}, {"size_in_bytes": 50}]},
            {"actions_caches": [{"size_in_bytes": 25}]},
        ]
        total = _safe_total_cache_bytes("acme", "ghp_test", ["repo-a", "repo-b"])
    assert total == 175


def test_cache_job_success_rate_mixed(db):
    from src.routers.analytics import _cache_job_success_rate

    for status in ("done", "done", "done", "failed"):
        job = Job(job_type="github.clear_actions_cache", payload="{}", status=status)
        db.add(job)
    db.commit()
    assert _cache_job_success_rate(db) == 0.75


def test_cache_job_success_rate_zero_jobs(db):
    from src.routers.analytics import _cache_job_success_rate

    assert _cache_job_success_rate(db) == 0.0


def test_cache_job_success_rate_ignores_other_job_types(db):
    from src.routers.analytics import _cache_job_success_rate

    job = Job(job_type="some.other.job", payload="{}", status="failed")
    db.add(job)
    db.commit()
    assert _cache_job_success_rate(db) == 0.0
