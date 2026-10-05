"""Tests for the analytics router."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import scan_results_repo, installation_repo, org_membership_repo, org_repo
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
    # exception must be logged, not silently swallowed
    mock_logger.exception.assert_called_once_with("analytics_overview failed")


# ── personal-account parity ─────────────────────────────────────────────────────

def test_personal_overview_runs_checks_for_user_account(http):
    """A personal (User-type) account gets a real scan, with account_type threaded into
    get_overview so org-only checks come back not_applicable."""
    with (
        patch("src.routers.analytics.get_account_type", return_value="User") as mock_account_type,
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as mock_overview,
    ):
        resp = http.post(
            "/me/analytics/overview",
            json={"owner": "octocat", "token": "ghp_test"},
        )
    assert resp.status_code == 200
    mock_account_type.assert_called_once()
    mock_overview.assert_called_once_with(owner="octocat", token="ghp_test", account_type="User", score_hygiene=None)


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


def test_org_overview_admin_can_scan_with_their_own_token(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="admin")
    with patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW):
        resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 200


def test_org_overview_member_cannot_scan_with_their_own_token_on_a_pat_only_org(http, db, mock_user):
    """A member-supplied PAT could be deliberately low-privilege, turning every check into an error and
    dragging down the stored score that feeds the badge, score API and alerts (#582)."""
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    with patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as overview:
        resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_lowpriv"})
    assert resp.status_code == 403
    overview.assert_not_called()
    assert scan_results_repo.latest_with_checks(db, "acme", org.tenant_id) is None  # nothing stored


def test_org_overview_member_can_scan_through_the_installation_and_their_token_is_ignored(
    http, db, mock_user, monkeypatch
):
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
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as overview,
        patch("src.services.token_resolution.github_app.get_installation_token", return_value="minted-token"),
    ):
        resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_lowpriv"})
    assert resp.status_code == 200
    assert overview.call_args.kwargs["token"] == "minted-token"


def test_personal_overview_member_cannot_scan_their_org_with_their_own_token(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as overview,
    ):
        resp = http.post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_lowpriv"})
    assert resp.status_code == 403
    overview.assert_not_called()


def test_personal_overview_byo_token_scan_of_a_non_org_login_is_unaffected(http, db):
    with (
        patch("src.routers.analytics.get_account_type", return_value="User"),
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW),
    ):
        resp = http.post("/me/analytics/overview", json={"owner": "octocat", "token": "ghp_mine"})
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


def test_org_overview_fires_score_drop_notification_against_the_previous_scan(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="admin")
    scans = [{**MOCK_OVERVIEW, "score": 90}, {**MOCK_OVERVIEW, "score": 60}]
    with (
        patch("src.routers.analytics.get_overview", side_effect=scans),
        patch("src.routers.analytics.notifications.notify_score_drop") as notify,
    ):
        http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
        notify.assert_not_called()  # first scan: nothing to compare against
        http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})

    notify.assert_called_once_with(db, org.tenant_id, "acme", 90, 60)


def test_score_drop_ignores_another_tenants_scan_of_the_same_owner(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="admin")
    other_org = org_repo.get_or_create(db, github_login="other")
    scan_results_repo.insert(
        db, owner="acme", score=99, total_checks=6, failed_checks=0, checks=[], tenant_id=other_org.tenant_id
    )
    with (
        patch("src.routers.analytics.get_overview", return_value={**MOCK_OVERVIEW, "score": 50}),
        patch("src.routers.analytics.notifications.notify_score_drop") as notify,
    ):
        http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})

    notify.assert_not_called()


# ── per-org hygiene scoring (#561) ────────────────────────────────────────────

def _org_with_role(db, user, role):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role=role)
    return org


def test_hygiene_scoring_defaults_to_following_the_instance(http, db, mock_user):
    _org_with_role(db, mock_user, "admin")
    with patch("src.services.analytics_service.get_config", return_value="false"):
        body = http.get("/orgs/acme/hygiene-scoring").json()
    assert body == {"enabled": None, "effective": False, "instance_default": False}


def test_admin_sets_and_clears_the_org_override_and_it_is_audited(http, db, mock_user):
    from src.core.db import AuditLog

    org = _org_with_role(db, mock_user, "admin")
    with patch("src.services.analytics_service.get_config", return_value="false"):
        on = http.put("/orgs/acme/hygiene-scoring", json={"enabled": True}).json()
        assert on == {"enabled": True, "effective": True, "instance_default": False}
        db.refresh(org)
        assert org.score_hygiene_checks is True
        cleared = http.put("/orgs/acme/hygiene-scoring", json={"enabled": None}).json()
        assert cleared == {"enabled": None, "effective": False, "instance_default": False}
    assert db.query(AuditLog).filter(AuditLog.action == "hygiene_scoring.updated").count() == 2


def test_member_cannot_read_or_change_hygiene_scoring(http, db, mock_user):
    _org_with_role(db, mock_user, "member")
    assert http.get("/orgs/acme/hygiene-scoring").status_code == 403
    assert http.put("/orgs/acme/hygiene-scoring", json={"enabled": True}).status_code == 403


def test_org_scan_uses_the_orgs_hygiene_setting_not_the_instances(http, db, mock_user):
    org = _org_with_role(db, mock_user, "admin")
    org.score_hygiene_checks = True
    db.flush()
    with (
        patch("src.services.analytics_service.get_config", return_value="false"),
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as overview,
    ):
        http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert overview.call_args.kwargs["score_hygiene"] is True


def test_personal_scan_of_a_members_org_uses_the_orgs_setting_and_other_owners_use_the_instance(http, db, mock_user):
    org = _org_with_role(db, mock_user, "admin")
    org.score_hygiene_checks = True
    db.flush()
    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW) as overview,
    ):
        http.post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
        assert overview.call_args.kwargs["score_hygiene"] is True
        http.post("/me/analytics/overview", json={"owner": "octocat", "token": "ghp_test"})
        assert overview.call_args.kwargs["score_hygiene"] is None
