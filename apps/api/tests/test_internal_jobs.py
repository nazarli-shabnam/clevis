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
from src.services import job_refresh

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


def _job(db, tenant_id, *, status="processing", job_type="github.backfill_repo_events", payload=None, sign=True, expires_at=None):
    payload = payload or {"account_login": "acme"}
    job = Job(job_type=job_type, payload=json.dumps(payload), status=status, tenant_id=tenant_id)
    db.add(job)
    db.flush()
    field = job_refresh.ACCOUNT_FIELD.get(job_type)
    if sign and field and payload.get(field):
        exp = job_refresh.new_expiry() if expires_at is None else expires_at
        job.payload = json.dumps(
            {**payload, job_refresh.EXP_FIELD: exp, job_refresh.SIG_FIELD: job_refresh.sign(job.id, tenant_id, payload[field], exp)}
        )
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


def test_a_job_row_the_worker_rewrote_is_refused(http, db, configured):
    # The worker's DB role can UPDATE every jobs column; the API's signature must make that useless.
    victim = _org_with_installation(db)
    attacker = org_repo.get_or_create(db, github_login="attacker")
    installation_repo.create(
        db, account_login="attacker", account_type="Organization", auth_mode="app", installation_id=66, org_id=attacker.id
    )
    with patch("src.routers.internal_jobs.github_app.get_installation_token", return_value="x") as mint:
        # 1. tenant_id rewritten to the victim's: the signature was made for the attacker's tenant
        job_id = _job(db, attacker.tenant_id, payload={"account_login": "attacker"})
        db.get(Job, job_id).tenant_id = victim.tenant_id
        db.commit()
        assert _post(http, job_id).status_code == 404
        # 2. account rewritten in the payload (tenant untouched)
        job_id = _job(db, victim.tenant_id, payload={"account_login": "acme"})
        job = db.get(Job, job_id)
        job.payload = json.dumps({**json.loads(job.payload), "account_login": "attacker"})
        db.commit()
        assert _post(http, job_id).status_code == 404
        # 3. another job's signature copied onto this one
        donor = _job(db, attacker.tenant_id, payload={"account_login": "attacker"})
        stolen = json.loads(db.get(Job, donor).payload)[job_refresh.SIG_FIELD]
        target = _job(db, victim.tenant_id, payload={"account_login": "acme"})
        job = db.get(Job, target)
        job.payload = json.dumps({**json.loads(job.payload), job_refresh.SIG_FIELD: stolen})
        db.commit()
        assert _post(http, target).status_code == 404
        # 4. no signature at all (a job from before this change, or one the worker stripped)
        assert _post(http, _job(db, victim.tenant_id, sign=False)).status_code == 404
    mint.assert_not_called()


def test_enqueue_signs_the_jobs_that_can_be_refreshed(db):
    from src.repositories import job_repo

    org = org_repo.get_or_create(db, github_login="acme")
    job_id = job_repo.enqueue(db, "github.backfill_repo_events", {"account_login": "acme", "token": "t"}, org.tenant_id)
    payload = json.loads(db.get(Job, job_id).payload)
    exp = payload[job_refresh.EXP_FIELD]
    assert job_refresh.verify(payload[job_refresh.SIG_FIELD], exp, job_id, org.tenant_id, "ACME")  # login case-insensitive
    assert not job_refresh.verify(payload[job_refresh.SIG_FIELD], exp + 1, job_id, org.tenant_id, "ACME")  # exp is signed
    assert payload["token"] == "t" and payload["account_login"] == "acme"
    # No tenant, or a job type that never refreshes: no signature.
    assert job_refresh.SIG_FIELD not in json.loads(db.get(Job, job_repo.enqueue(db, "github.backfill_repo_events", {"account_login": "acme"}, None)).payload)
    assert job_refresh.SIG_FIELD not in json.loads(db.get(Job, job_repo.enqueue(db, "api.scan", {"actor": "a"}, org.tenant_id)).payload)


def test_an_expired_signature_is_refused_even_for_a_running_job(http, db, configured):
    import time

    org = _org_with_installation(db)
    job_id = _job(db, org.tenant_id, expires_at=int(time.time()) - 5)
    with patch("src.routers.internal_jobs.github_app.get_installation_token", return_value="x") as mint:
        assert _post(http, job_id).status_code == 404
    mint.assert_not_called()
