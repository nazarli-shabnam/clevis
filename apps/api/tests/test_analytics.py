"""Tests for the analytics router — B-02: async handler, B-10: error logging."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.routers.analytics import router


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)

MOCK_OVERVIEW = {
    "owner": "acme",
    "score": 80,
    "total_checks": 1,
    "failed_checks": 0,
    "repo_count": 4,
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
def mock_user(db):
    return _make_user(db, "test@example.com")


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


def test_personal_overview_requires_auth(db):
    a = FastAPI()
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(router)
    resp = TestClient(a).post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 401


def test_overview_returns_expected_shape(http):
    # Mock get_overview directly; anyio.to_thread.run_sync runs the lambda for real
    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW),
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 80
    assert body["repo_count"] == 4
    assert len(body["checks"]) == 1


def test_overview_github_http_error_returns_400(http):
    import httpx

    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch(
            "src.routers.analytics.get_overview",
            side_effect=httpx.HTTPStatusError(
                "not found",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            ),
        ),
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 400
    assert "GitHub API error" in resp.json()["detail"]


def test_overview_request_error_returns_503(http):
    import httpx

    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch(
            "src.routers.analytics.get_overview",
            side_effect=httpx.RequestError("timeout"),
        ),
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 503


def test_overview_unexpected_exception_logs_and_returns_500(http):
    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch(
            "src.routers.analytics.get_overview",
            side_effect=RuntimeError("unexpected"),
        ),
        patch("src.routers.analytics.logger") as mock_logger,
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "acme", "token": "ghp_test"},
        )
    assert resp.status_code == 500
    # B-10: exception must be logged, not silently swallowed
    mock_logger.exception.assert_called_once_with("analytics_overview failed")


# ── personal-account guard (issue #144) ────────────────────────────────────────

def test_personal_overview_rejects_user_account_with_422(http):
    with (
        patch("src.routers.analytics.get_account_type", return_value="User") as mock_account_type,
        patch("src.routers.analytics.get_overview") as mock_overview,
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "octocat", "token": "ghp_test"},
        )
    assert resp.status_code == 422
    assert "Personal GitHub accounts aren't supported" in resp.json()["detail"]
    mock_account_type.assert_called_once()
    mock_overview.assert_not_called()


def test_personal_overview_account_type_http_error_returns_400(http):
    import httpx

    with patch(
        "src.routers.analytics.get_account_type",
        side_effect=httpx.HTTPStatusError(
            "not found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        ),
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "octocat", "token": "ghp_test"},
        )
    assert resp.status_code == 400
    assert "GitHub API error" in resp.json()["detail"]


def test_personal_overview_account_type_request_error_returns_503(http):
    import httpx

    with patch(
        "src.routers.analytics.get_account_type",
        side_effect=httpx.RequestError("timeout"),
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "octocat", "token": "ghp_test"},
        )
    assert resp.status_code == 503


# ── org-scoped ────────────────────────────────────────────────────────────────

def test_org_overview_outsider_forbidden(http, db):
    org_repo.get_or_create(db, github_login="acme")
    resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 403


def test_org_overview_member_ok(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    with patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW):
        resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 200


def test_org_overview_owner_mismatch_forbidden(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    resp = http.post("/orgs/acme/analytics/overview", json={"owner": "someone-else", "token": "ghp_test"})
    assert resp.status_code == 403


# ── GitHub App installation-token fallback ─────────────────────────────────────

def test_org_overview_uses_installation_token_when_no_client_token(http, db, mock_user, monkeypatch):
    from pydantic import SecretStr

    from src.core.config import settings

    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_app_private_key", SecretStr("dummy-pem"))
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with (
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as mock_overview,
        patch("src.services.token_resolution.github_app.get_installation_token", return_value="minted-token"),
    ):
        resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme"})
    assert resp.status_code == 200
    assert mock_overview.call_args.kwargs["token"] == "minted-token"


def test_org_overview_no_installation_and_no_token_returns_400(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme"})
    assert resp.status_code == 400


def test_personal_overview_no_installation_and_no_token_returns_400(http):
    resp = http.post("/me/analytics/overview", json={"owner": "acme"})
    assert resp.status_code == 400
