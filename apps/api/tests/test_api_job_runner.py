"""`?async=1` on the long-running endpoints: 202 + a tracked job instead of a request-long GitHub loop.

The runner's pool is swapped for an inline executor and its session for the test session, so the job's
whole life (insert as `running`, work, `done`/`failed`) is observable through `GET /jobs/{id}`.
"""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import Job, User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers import analytics, jobs, remediation_bulk
from src.services import api_job_runner

CHECK = "repository_secret_scanning_enabled"


class _Inline:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture()
def inline_jobs(db, monkeypatch):
    @contextmanager
    def shared_session():
        yield db

    monkeypatch.setattr(api_job_runner, "_executor", _Inline())
    monkeypatch.setattr(api_job_runner, "_open_session", shared_session)


@pytest.fixture()
def acme(db):
    admin = User(email="admin@e.com", name=None, password_hash=None, is_workspace_admin=False)
    other = User(email="other@e.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add_all([admin, other])
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    return {"org": org, "admin": admin, "other": other}


def _client(db, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(remediation_bulk.router)
    app.include_router(analytics.router)
    app.include_router(jobs.router, prefix="/jobs")
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class _GitHub:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def request(self, method, path, params=None, json=None):
        if self.error:
            raise self.error
        if method == "GET":
            return {"default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "disabled"}}}
        return {}


def _forbidden():
    return httpx.HTTPStatusError(
        "403", request=httpx.Request("GET", "https://api.github.com"), response=httpx.Response(403)
    )


BODY = {"token": "ghp_x", "check_id": CHECK, "repos": ["a", "b"], "dry_run": False}
URL = "/orgs/acme/security/remediate/bulk?async=1"


def test_async_bulk_remediate_returns_202_and_the_job_holds_the_normal_result(db, acme, inline_jobs):
    client = _client(db, acme["admin"])
    with patch("src.routers.remediation_bulk.GitHubClient", return_value=_GitHub()):
        resp = client.post(URL, json=BODY)

    assert resp.status_code == 202
    job = client.get(f"/jobs/{resp.json()['job_id']}").json()
    assert job["status"] == "done"
    result = json.loads(job["result"])
    assert result["check_id"] == CHECK
    assert [i["status"] for i in result["items"]] == ["applied", "applied"]


def test_async_job_is_never_queued_for_the_worker(db, acme, inline_jobs):
    """The worker claims every `queued` row and fails types it has no handler for."""
    seen = {}
    real = api_job_runner._run

    def spy(job_id, *a):
        seen["status"] = db.get(Job, job_id).status
        real(job_id, *a)

    with patch.object(api_job_runner, "_run", spy), patch(
        "src.routers.remediation_bulk.GitHubClient", return_value=_GitHub()
    ):
        _client(db, acme["admin"]).post(URL, json=BODY)

    assert seen["status"] == "running"


def test_async_whole_batch_403_keeps_the_permission_hint_in_the_job_result(db, acme, inline_jobs):
    client = _client(db, acme["admin"])
    with patch("src.routers.remediation_bulk.GitHubClient", return_value=_GitHub(_forbidden())):
        job_id = client.post(URL, json=BODY).json()["job_id"]

    result = json.loads(client.get(f"/jobs/{job_id}").json()["result"])
    assert result["hint"] and "403" in result["hint"]


def test_async_job_that_raises_is_marked_failed_without_leaking_the_error(db, acme, inline_jobs):
    client = _client(db, acme["admin"])
    with patch("src.routers.remediation_bulk.GitHubClient", side_effect=RuntimeError("boom ghp_secret")):
        job_id = client.post(URL, json=BODY).json()["job_id"]

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["result"] == "Internal error"


def test_job_is_only_readable_by_the_user_who_started_it(db, acme, inline_jobs):
    with patch("src.routers.remediation_bulk.GitHubClient", return_value=_GitHub()):
        job_id = _client(db, acme["admin"]).post(URL, json=BODY).json()["job_id"]

    assert _client(db, acme["other"]).get(f"/jobs/{job_id}").status_code == 404


def test_sync_call_without_the_flag_is_unchanged(db, acme):
    client = _client(db, acme["admin"])
    with patch("src.routers.remediation_bulk.GitHubClient", return_value=_GitHub()):
        resp = client.post("/orgs/acme/security/remediate/bulk", json=BODY)

    assert resp.status_code == 200 and resp.json()["items"][0]["status"] == "applied"


def test_async_org_scan_persists_the_score_in_the_job(db, acme, inline_jobs):
    overview = {
        "owner": "acme", "score": 100, "total_checks": 1, "failed_checks": 0, "repo_count": 1,
        "checks": [{"id": "c", "title": "C", "status": "pass", "value": {}, "severity": "low", "scored": True}],
    }
    client = _client(db, acme["admin"])
    with patch("src.routers.analytics.get_overview", return_value=overview), patch(
        "src.routers.analytics.resolve_org_token", return_value="tok"
    ):
        resp = client.post("/orgs/acme/analytics/overview?async=1", json={"owner": "acme"})
        assert resp.status_code == 202
        job = client.get(f"/jobs/{resp.json()['job_id']}").json()

    assert job["status"] == "done" and json.loads(job["result"])["score"] == 100
    assert client.get("/orgs/acme/analytics/history").json()[0]["score"] == 100


def test_reap_orphans_fails_only_old_running_api_jobs(db):
    def make(job_type, status, age):
        j = Job(job_type=job_type, payload="{}", status=status)
        db.add(j)
        db.commit()
        db.query(Job).filter(Job.id == j.id).update({"updated_at": datetime.now(timezone.utc) - age})
        db.commit()
        return j.id

    old = make(api_job_runner.JOB_TYPE_REMEDIATE, "running", timedelta(hours=3))
    fresh = make(api_job_runner.JOB_TYPE_REMEDIATE, "running", timedelta(minutes=5))
    foreign = make("github.clear_actions_cache", "running", timedelta(hours=3))

    assert api_job_runner.reap_orphans(db) == 1
    db.expire_all()
    assert db.get(Job, old).status == "failed"
    assert db.get(Job, fresh).status == "running"
    assert db.get(Job, foreign).status == "running"
