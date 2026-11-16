"""POST /internal/jobs/{id}/installation-token: a worker-only fresh token for a running job."""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.core.config import settings
from src.core.db import Job, get_db
from src.repositories import installation_repo, org_repo
from src.routers.internal_jobs import router

SECRET = "w" * 40


@pytest.fixture()
def http(db):
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, "worker_api_token", SecretStr(SECRET))


def _job(db, tenant_id, *, status="processing", job_type="github.backfill_repo_events", payload=None):
    job = Job(job_type=job_type, payload=json.dumps(payload or {"account_login": "acme"}), status=status, tenant_id=tenant_id)
    db.add(job)
    db.commit()
    return job.id


def _org_with_installation(db):
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    return org


def _post(http, job_id, secret=SECRET):
    headers = {"Authorization": f"Bearer {secret}"} if secret is not None else {}
    return http.post(f"/internal/jobs/{job_id}/installation-token", headers=headers)


def test_the_route_is_off_without_a_configured_secret(http, db, monkeypatch):
    for value in (None, SecretStr("")):
        monkeypatch.setattr(settings, "worker_api_token", value)
        # An empty secret must never match an empty bearer value.
        assert _post(http, 1, secret="").status_code == 404


def test_a_wrong_or_missing_credential_is_refused(http, db, configured):
    assert _post(http, 1, secret="nope").status_code == 401
    assert _post(http, 1, secret=None).status_code == 401


def test_a_running_job_gets_a_fresh_token_for_its_own_installation(http, db, configured):
    org = _org_with_installation(db)
    job_id = _job(db, org.tenant_id)
    with patch("src.routers.internal_jobs.github_app.get_installation_token", return_value="fresh") as mint:
        resp = _post(http, job_id)
    assert resp.status_code == 200 and resp.json() == {"token": "fresh"}
    assert mint.call_args.args[0] == 42


@pytest.mark.parametrize("status", ["queued", "done", "failed"])
def test_a_job_that_is_not_running_gets_nothing(http, db, configured, status):
    org = _org_with_installation(db)
    assert _post(http, _job(db, org.tenant_id, status=status)).status_code == 404


def test_another_tenants_installation_is_never_used(http, db, configured):
    _org_with_installation(db)
    other = org_repo.get_or_create(db, github_login="other")
    job_id = _job(db, other.tenant_id, payload={"account_login": "acme"})  # names acme, but belongs to other
    assert _post(http, job_id).status_code == 404


def test_a_job_without_an_installation_or_of_another_type_gets_nothing(http, db, configured):
    org = org_repo.get_or_create(db, github_login="acme")
    assert _post(http, _job(db, org.tenant_id)).status_code == 404  # PAT-backed: no installation
    assert _post(http, _job(db, org.tenant_id, job_type="api.scan")).status_code == 404
    assert _post(http, 999999).status_code == 404


def test_a_mint_failure_is_a_502_so_the_worker_falls_back(http, db, configured):
    org = _org_with_installation(db)
    job_id = _job(db, org.tenant_id)
    with patch("src.routers.internal_jobs.github_app.get_installation_token", side_effect=RuntimeError("boom")):
        assert _post(http, job_id).status_code == 502
