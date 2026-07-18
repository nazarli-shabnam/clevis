"""Tests for the scan history endpoints and insert-after-scan persistence (Phase 10)."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers.analytics import router

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


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


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


def test_personal_history_requires_auth(db):
    a = FastAPI()
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(router)
    resp = TestClient(a).get("/me/analytics/history?owner=acme")
    assert resp.status_code == 401


def test_personal_history_returns_seeded_rows_newest_first(http, db):
    scan_results_repo.insert(db, owner="acme", score=60, total_checks=3, failed_checks=1, checks=[])
    scan_results_repo.insert(db, owner="acme", score=80, total_checks=3, failed_checks=0, checks=[])
    resp = http.get("/me/analytics/history?owner=acme")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["score"] == 80
    assert body[1]["score"] == 60


def test_personal_history_only_returns_matching_owner(http, db):
    scan_results_repo.insert(db, owner="acme", score=80, total_checks=3, failed_checks=0, checks=[])
    scan_results_repo.insert(db, owner="other-org", score=50, total_checks=3, failed_checks=1, checks=[])
    resp = http.get("/me/analytics/history?owner=acme")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["owner"] == "acme"


def test_scan_results_repo_list_recent_respects_limit(db):
    for score in range(5):
        scan_results_repo.insert(db, owner="acme", score=score, total_checks=1, failed_checks=0, checks=[])
    rows = scan_results_repo.list_recent(db, owner="acme", limit=2)
    assert len(rows) == 2
    assert rows[0]["score"] == 4  # newest first
    assert rows[1]["score"] == 3


def test_org_history_outsider_forbidden(http, db):
    org_repo.get_or_create(db, github_login="acme")
    resp = http.get("/orgs/acme/analytics/history")
    assert resp.status_code == 403


def test_org_history_unknown_org_returns_404(http):
    resp = http.get("/orgs/does-not-exist/analytics/history")
    assert resp.status_code == 404


def test_org_history_member_ok(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    scan_results_repo.insert(db, owner="acme", score=80, total_checks=3, failed_checks=0, checks=[])
    resp = http.get("/orgs/acme/analytics/history")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ── insert-after-scan integration ────────────────────────────────────────────


def test_personal_overview_scan_persists_a_history_row(http, db):
    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW),
    ):
        resp = http.post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 200

    history = http.get("/me/analytics/history?owner=acme")
    assert history.status_code == 200
    body = history.json()
    assert len(body) == 1
    assert body[0]["score"] == 80
    assert body[0]["total_checks"] == 1
    assert body[0]["failed_checks"] == 0


def test_org_overview_scan_persists_a_history_row(http, db, mock_user):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    with patch("src.routers.analytics.get_overview", return_value=MOCK_OVERVIEW):
        resp = http.post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 200

    history = http.get("/orgs/acme/analytics/history")
    assert history.status_code == 200
    assert len(history.json()) == 1


def test_overview_error_does_not_persist_a_history_row(http):
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
        resp = http.post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_test"})
    assert resp.status_code == 400

    history = http.get("/me/analytics/history?owner=acme")
    assert history.json() == []
