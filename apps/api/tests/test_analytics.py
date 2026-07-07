"""Tests for the analytics router — B-02: async handler, B-10: error logging."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.routers.analytics import router

_MOCK_USER = UserOut(id=1, email="test@example.com", name=None, is_owner=True)
_MOCK_NON_OWNER = UserOut(id=2, email="member@example.com", name=None, is_owner=False)

MOCK_OVERVIEW = {
    "owner": "acme",
    "score": 80,
    "total_checks": 1,
    "failed_checks": 0,
    "checks": [
        {
            "id": "organization_members_mfa_required",
            "title": "Organization requires 2FA/MFA",
            "severity": "high",
            "remediation": "Enable 2FA.",
            "status": "pass",
            "value": True,
        }
    ],
}


@pytest.fixture()
def app():
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: _MOCK_USER
    a.include_router(router, prefix="/analytics")
    return a


@pytest.fixture()
def http(app):
    return TestClient(app)


@pytest.fixture()
def non_owner_http():
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: _MOCK_NON_OWNER
    a.include_router(router, prefix="/analytics")
    return TestClient(a)


def test_overview_non_owner_forbidden(non_owner_http):
    resp = non_owner_http.post(
        "/analytics/overview",
        json={"owner": "acme", "token": "ghp_test"},
    )
    assert resp.status_code == 403


def test_overview_returns_expected_shape(http):
    # Mock get_overview directly; anyio.to_thread.run_sync runs the lambda for real
    with patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW):
        resp = http.post(
            "/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 80
    assert len(body["checks"]) == 1


def test_overview_github_http_error_returns_400(http):
    import httpx

    with patch(
        "src.routers.analytics.get_overview",
        side_effect=httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        ),
    ):
        resp = http.post(
            "/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 400
    assert "GitHub API error" in resp.json()["detail"]


def test_overview_request_error_returns_503(http):
    import httpx

    with patch(
        "src.routers.analytics.get_overview",
        side_effect=httpx.RequestError("timeout"),
    ):
        resp = http.post(
            "/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 503


def test_overview_unexpected_exception_logs_and_returns_500(http):
    with (
        patch(
            "src.routers.analytics.get_overview",
            side_effect=RuntimeError("unexpected"),
        ),
        patch("src.routers.analytics.logger") as mock_logger,
    ):
        resp = http.post(
            "/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 500
    # B-10: exception must be logged, not silently swallowed
    mock_logger.exception.assert_called_once_with("analytics_overview failed")
