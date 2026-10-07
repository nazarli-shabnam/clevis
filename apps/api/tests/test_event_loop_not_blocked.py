"""Blocking DB and Redis work in async handlers runs off the event loop (#523).

A synchronous commit or a Redis XADD (2s timeout) executed directly in an ``async def`` handler
stalls every in-flight request on the worker, health checks included. Each test wraps the blocking
call, records whether an event loop was running on the calling thread, and calls through.
"""
import asyncio
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers import webhooks as webhooks_module
from src.routers.analytics import router as analytics_router
from src.routers.api_tokens import router as tokens_router
from src.routers.webhooks import router as webhooks_router


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _Recorder:
    """Wraps a callable; remembers, per call, whether it ran on a thread with a running loop."""

    def __init__(self, fn=None, result=None):
        self.fn = fn
        self.result = result
        self.on_loop: list[bool] = []

    def __call__(self, *args, **kwargs):
        self.on_loop.append(_on_event_loop())
        return self.fn(*args, **kwargs) if self.fn is not None else self.result

    def assert_called_off_the_loop(self):
        assert self.on_loop, "the blocking call was never made"
        assert not any(self.on_loop), "a blocking call ran on the event loop"


def _user(db, email: str) -> UserOut:
    row = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return UserOut(id=row.id, email=row.email, name=row.name, is_workspace_admin=False)


OVERVIEW = {"owner": "acme", "score": 80, "total_checks": 1, "failed_checks": 0, "repo_count": 4, "checks": []}


# ── webhook ──────────────────────────────────────────────────────────────────


def test_webhook_persists_and_enqueues_off_the_event_loop(db, monkeypatch):
    monkeypatch.setattr(settings, "github_app_webhook_secret", SecretStr("s3cret"))
    ingest = _Recorder(webhooks_module._handle_ingested_event)

    class _Redis:
        def __init__(self):
            self.xadd = _Recorder(result="1-0")

    redis = _Redis()
    monkeypatch.setattr(webhooks_module, "_handle_ingested_event", ingest)
    monkeypatch.setattr(webhooks_module, "get_redis_client", lambda: redis)
    app = FastAPI()
    app.include_router(webhooks_router)
    app.dependency_overrides[get_db] = lambda: db

    body = json.dumps({"action": "opened", "installation": {"id": 4242}}).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    resp = TestClient(app).post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-1"},
    )

    assert resp.status_code == 200
    ingest.assert_called_off_the_loop()
    redis.xadd.assert_called_off_the_loop()


def test_webhook_still_rejects_a_bad_signature_before_doing_any_blocking_work(db, monkeypatch):
    monkeypatch.setattr(settings, "github_app_webhook_secret", SecretStr("s3cret"))
    ingest = _Recorder(result=None)
    monkeypatch.setattr(webhooks_module, "_dispatch_event", ingest)
    app = FastAPI()
    app.include_router(webhooks_router)
    app.dependency_overrides[get_db] = lambda: db

    resp = TestClient(app).post(
        "/webhooks/github", content=b"{}", headers={"X-Hub-Signature-256": "sha256=bad", "X-GitHub-Event": "push"}
    )

    assert resp.status_code == 401
    assert ingest.on_loop == []


# ── analytics ────────────────────────────────────────────────────────────────


def test_org_overview_reads_and_persists_the_scan_off_the_event_loop(db):
    org = org_repo.get_or_create(db, github_login="acme")
    user = _user(db, "member@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member")
    baseline = _Recorder(scan_results_repo.latest_with_checks)
    persist = _Recorder(lambda *a, **k: None)
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(analytics_router)

    with (
        patch("src.routers.analytics.resolve_org_token", return_value="ghp"),
        patch("src.routers.analytics.get_overview", return_value=OVERVIEW),
        patch("src.routers.analytics.scan_results_repo.latest_with_checks", baseline),
        patch("src.services.scan_service.persist_scan", persist),
    ):
        resp = TestClient(app).post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})

    assert resp.status_code == 200
    baseline.assert_called_off_the_loop()
    persist.assert_called_off_the_loop()


def test_cockpit_reads_scan_history_and_job_rate_off_the_event_loop(db):
    user = _user(db, "cockpit-loop@example.com")
    list_recent = _Recorder(scan_results_repo.list_recent)
    job_rate = _Recorder(result=0.5)
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(analytics_router)

    # An owner the caller has scanned, so the history read actually happens.
    scan_results_repo.insert(
        db, owner="acme", score=70, total_checks=1, failed_checks=0, checks=[],
        tenant_id=org_repo.get_or_create(db, github_login="acme").tenant_id, scanned_by_user_id=user.id,
    )
    with (
        patch("src.routers.analytics.resolve_owner_token", return_value="ghp"),
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics._safe_list_repos", return_value=[]),
        patch("src.routers.analytics._safe_member_count", return_value=(1, True)),
        patch("src.routers.analytics._cockpit_events_and_commit_activity", return_value=([], False, False, ([0] * 4, [0] * 52, True))),
        patch("src.routers.analytics._safe_open_pr_count", return_value=(0, True)),
        patch("src.routers.analytics._safe_pr_merge_rate_4w", return_value=[]),
        patch("src.routers.analytics._safe_total_cache_bytes", return_value=(0, True)),
        patch("src.routers.analytics._safe_milestones", return_value=([], [])),
        patch("src.routers.analytics._safe_pr_cycle_time_8w", return_value=[]),
        patch("src.routers.analytics._safe_release_cadence_4w", return_value=[]),
        patch("src.routers.analytics.scan_results_repo.list_recent", list_recent),
        patch("src.routers.analytics._cache_job_success_rate", job_rate),
    ):
        resp = TestClient(app).get("/me/analytics/cockpit/acme", headers={"X-GitHub-Token": "ghp"})

    assert resp.status_code == 200, resp.text
    list_recent.assert_called_off_the_loop()
    job_rate.assert_called_off_the_loop()


# ── api token scan ───────────────────────────────────────────────────────────


def test_api_token_scan_persists_off_the_event_loop(db):
    org = org_repo.get_or_create(db, github_login="acme")
    admin = _user(db, "tok-loop-admin@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    mgmt = FastAPI()
    mgmt.include_router(tokens_router)
    mgmt.dependency_overrides[get_db] = lambda: db
    mgmt.dependency_overrides[require_auth] = lambda: admin
    db.execute(text(f"SET app.user_id = {admin.id}"))
    created = TestClient(mgmt).post("/orgs/acme/api-tokens", json={"name": "ci"})
    assert created.status_code == 201
    token = created.json()["token"]
    latest = _Recorder(scan_results_repo.latest_with_checks)
    app = FastAPI()
    app.include_router(tokens_router)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch("src.routers.api_tokens.resolve_org_token", return_value="ghs_x"),
        patch("src.routers.analytics.get_overview", return_value=OVERVIEW),
        patch("src.routers.api_tokens.scan_results_repo.latest_with_checks", latest),
    ):
        resp = TestClient(app).post("/api/v1/orgs/acme/scan", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    latest.assert_called_off_the_loop()
