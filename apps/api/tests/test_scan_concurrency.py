"""Interactive scans must not hold a pooled DB connection across the GitHub phase, and one caller must not
be able to run unbounded scans at once (#791)."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.core import db as db_module
from src.repositories import org_membership_repo, org_repo
from src.routers import analytics
from src.services import scan_limiter

OVERVIEW = {
    "owner": "acme", "score": 100, "total_checks": 1, "failed_checks": 0, "repo_count": 1,
    "checks": [{"id": "c", "title": "C", "status": "pass", "value": {}, "severity": "low", "remediation": "n/a", "scored": True}],
}


@pytest.fixture()
def acme(db):
    admin = User(email="admin@e.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add(admin)
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    return {"org": org, "admin": admin}


def _client(db, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _setting(db, name: str):
    return db.execute(text(f"SELECT current_setting('{name}', true)")).scalar()


def test_scan_slot_caps_concurrent_holders_and_frees_on_exit():
    with patch.object(scan_limiter, "MAX_CONCURRENT_PER_KEY", 2):
        with scan_limiter.scan_slot("k"), scan_limiter.scan_slot("k"):
            with pytest.raises(HTTPException) as exc:
                with scan_limiter.scan_slot("k"):
                    pass
            assert exc.value.status_code == 429
            with scan_limiter.scan_slot("other"):  # another key is unaffected
                pass
        with scan_limiter.scan_slot("k"):  # slots were freed
            pass
    assert "k" not in scan_limiter._active


def test_scan_slot_is_freed_when_the_scan_raises():
    with pytest.raises(RuntimeError):
        with scan_limiter.scan_slot("boom"):
            raise RuntimeError
    assert "boom" not in scan_limiter._active


def test_org_scan_releases_the_connection_before_github_and_restores_context_to_persist(db, acme):
    seen = {}
    real_release = db_module.release_connection
    state = {"released": False}

    def release(session):
        real_release(session)
        state["released"] = True

    def overview(**kwargs):
        seen["released_before_github"] = state["released"]
        seen["context_during_github"] = (_setting(db, "app.tenant_id"), _setting(db, "app.user_id"))
        return OVERVIEW

    def persist(session, org, result, tenant_id, **kw):
        seen["tenant_at_persist"] = _setting(session, "app.tenant_id")
        seen["user_at_persist"] = _setting(session, "app.user_id")

    with patch("src.routers.analytics.release_connection", release), patch(
        "src.routers.analytics.get_overview", side_effect=overview
    ), patch("src.routers.analytics.resolve_org_token", return_value="tok"), patch(
        "src.routers.analytics.scan_service.persist_scan_and_alert", side_effect=persist
    ):
        resp = _client(db, acme["admin"]).post("/orgs/acme/analytics/overview", json={"owner": "acme"})

    assert resp.status_code == 200
    assert seen["released_before_github"] is True
    assert seen["context_during_github"] in {("", ""), (None, None)}  # nothing leaks onto the pooled connection
    assert seen["tenant_at_persist"] == str(acme["org"].tenant_id)
    assert seen["user_at_persist"] == str(acme["admin"].id)


def test_personal_scan_releases_the_connection_before_any_github_call(db, acme):
    order = []
    real_release = db_module.release_connection

    def release(session):
        real_release(session)
        order.append("release")

    with patch("src.routers.analytics.release_connection", release), patch(
        "src.routers.analytics.get_account_type", side_effect=lambda **k: order.append("account_type") or "Organization"
    ), patch("src.routers.analytics.get_overview", side_effect=lambda **k: order.append("scan") or OVERVIEW), patch(
        "src.routers.analytics.resolve_owner_token", return_value="tok"
    ):
        resp = _client(db, acme["admin"]).post("/me/analytics/overview", json={"owner": "acme"})

    assert resp.status_code == 200
    assert order == ["release", "account_type", "scan"]


def test_scan_beyond_the_per_account_cap_gets_429_without_calling_github(db, acme):
    with patch.object(scan_limiter, "MAX_CONCURRENT_PER_KEY", 0), patch(
        "src.routers.analytics.get_overview"
    ) as overview, patch("src.routers.analytics.resolve_org_token", return_value="tok"):
        resp = _client(db, acme["admin"]).post("/orgs/acme/analytics/overview", json={"owner": "acme"})

    assert resp.status_code == 429
    overview.assert_not_called()
