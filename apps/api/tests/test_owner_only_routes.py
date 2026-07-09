"""Workspace-admin-only enforcement for jobs/audit/tokens (instance-wide data with no
per-org column to scope by), plus org-role enforcement for the actions-cache routes.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.actions_cache import router as cache_router
from src.routers.audit import router as audit_router
from src.routers.jobs import router as jobs_router
from src.routers.tokens import router as tokens_router

_OWNER = UserOut(id=1, email="owner@example.com", name=None, is_workspace_admin=True)
_NON_OWNER = UserOut(id=2, email="member@example.com", name=None, is_workspace_admin=False)


def _client(router, db, user, prefix=""):
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


# ── jobs ──────────────────────────────────────────────────────────────────────

def test_jobs_owner_ok(db):
    resp = _client(jobs_router, db, _OWNER, prefix="/jobs").get("/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_jobs_non_owner_forbidden(db):
    resp = _client(jobs_router, db, _NON_OWNER, prefix="/jobs").get("/jobs")
    assert resp.status_code == 403


# ── audit ─────────────────────────────────────────────────────────────────────

def test_audit_owner_ok(db):
    resp = _client(audit_router, db, _OWNER, prefix="/audit").get("/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_audit_non_owner_forbidden(db):
    resp = _client(audit_router, db, _NON_OWNER, prefix="/audit").get("/audit")
    assert resp.status_code == 403


# ── tokens ────────────────────────────────────────────────────────────────────

def test_tokens_list_owner_ok(db):
    resp = _client(tokens_router, db, _OWNER, prefix="/tokens").get("/tokens")
    assert resp.status_code == 200
    assert resp.json() == []


def test_tokens_list_non_owner_forbidden(db):
    resp = _client(tokens_router, db, _NON_OWNER, prefix="/tokens").get("/tokens")
    assert resp.status_code == 403


def test_tokens_resolve_non_owner_forbidden(db):
    resp = _client(tokens_router, db, _NON_OWNER, prefix="/tokens").post(
        "/tokens/resolve", json={"org": "acme"}
    )
    assert resp.status_code == 403


# ── actions cache ─────────────────────────────────────────────────────────────
# Org-role dependencies are resolved before the handler body runs, so a non-member/
# non-admin never reaches the real GitHub API call — no need to mock GitHubClient here.

@pytest.fixture()
def cache_app(db):
    app = FastAPI()
    app.include_router(cache_router)
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture()
def acme_membership(db):
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=_OWNER.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=_NON_OWNER.id, role="member")
    return org


def test_org_actions_cache_list_outsider_forbidden(cache_app, acme_membership):
    outsider = UserOut(id=99, email="outsider@example.com", name=None, is_workspace_admin=False)
    cache_app.dependency_overrides[require_auth] = lambda: outsider
    resp = TestClient(cache_app).post(
        "/orgs/acme/repos/acme/widget/actions-caches", json={"token": "ghp_test"}
    )
    assert resp.status_code == 403


def test_org_actions_cache_clear_member_forbidden(cache_app, acme_membership):
    """Members can list caches but clearing requires org-admin."""
    cache_app.dependency_overrides[require_auth] = lambda: _NON_OWNER
    resp = TestClient(cache_app).post(
        "/orgs/acme/repos/acme/widget/actions-caches/clear",
        json={"token": "ghp_test", "actor": "member@example.com"},
    )
    assert resp.status_code == 403


def test_org_actions_cache_owner_mismatch_forbidden(cache_app, acme_membership):
    """An org admin can't act on a different GitHub owner than the org they're scoped to."""
    cache_app.dependency_overrides[require_auth] = lambda: _OWNER
    resp = TestClient(cache_app).post(
        "/orgs/acme/repos/someone-else/widget/actions-caches", json={"token": "ghp_test"}
    )
    assert resp.status_code == 403


def test_personal_actions_cache_requires_auth(cache_app):
    resp = TestClient(cache_app).post(
        "/me/repos/acme/widget/actions-caches", json={"token": "ghp_test"}
    )
    assert resp.status_code == 401
