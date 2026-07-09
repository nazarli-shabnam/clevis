"""Owner-only enforcement for routes that expose installation/token/analytics/job/audit data.

Until a proper multi-tenant org model exists, all of this data effectively belongs to the
single instance owner — a plain registered/OAuth account should only be able to manage its
own profile (/auth/me), not read or mutate anyone else's connected orgs, tokens, or logs.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.routers.actions_cache import router as cache_router
from src.routers.audit import router as audit_router
from src.routers.jobs import router as jobs_router
from src.routers.tokens import router as tokens_router

_OWNER = UserOut(id=1, email="owner@example.com", name=None, is_owner=True)
_NON_OWNER = UserOut(id=2, email="member@example.com", name=None, is_owner=False)


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
# Auth is resolved before the handler body runs, so a non-owner never reaches the
# real GitHub API call — no need to mock GitHubClient for this check.

@pytest.fixture()
def cache_app():
    app = FastAPI()
    app.include_router(cache_router, prefix="/repos")
    app.dependency_overrides[get_db] = lambda: None
    return app


def test_actions_cache_list_non_owner_forbidden(cache_app):
    cache_app.dependency_overrides[require_auth] = lambda: _NON_OWNER
    resp = TestClient(cache_app).post(
        "/repos/acme/widget/actions-caches", json={"token": "ghp_test"}
    )
    assert resp.status_code == 403


def test_actions_cache_clear_non_owner_forbidden(cache_app):
    cache_app.dependency_overrides[require_auth] = lambda: _NON_OWNER
    resp = TestClient(cache_app).post(
        "/repos/acme/widget/actions-caches/clear",
        json={"token": "ghp_test", "actor": "member@example.com"},
    )
    assert resp.status_code == 403
