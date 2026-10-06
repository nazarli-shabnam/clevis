"""The 0059 backfill attributes old jobs to a tenant only where that is unambiguous."""

import importlib.util
import json
from pathlib import Path

import pytest

from src.core.db import Job
from src.repositories import org_repo

_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0059_add_jobs_tenant_id.py"


@pytest.fixture()
def migration(db, monkeypatch):
    spec = importlib.util.spec_from_file_location("migration_0059", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # run the backfill on the test's own connection/transaction instead of an alembic context
    monkeypatch.setattr(module.op, "get_bind", lambda: db.connection())
    return module


def _job(db, job_type, payload, tenant_id=None):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    job = Job(job_type=job_type, payload=raw, status="done", tenant_id=tenant_id)
    db.add(job)
    db.commit()
    return job.id


def _tenant_of(db, job_id):
    db.expire_all()
    return db.query(Job).filter(Job.id == job_id).one().tenant_id


def test_backfill_attributes_only_what_is_unambiguous(db, migration):
    acme = org_repo.get_or_create(db, github_login="Acme")
    dup1 = org_repo.get_or_create(db, github_login="dup")
    dup2 = org_repo.get_or_create(db, github_login="DUP")

    ids = {
        "backfill_by_tenant": _job(db, "github.backfill_repo_events", {"tenant_id": acme.tenant_id, "token": "x"}),
        "reconcile_by_tenant": _job(db, "github.reconcile_org_membership", {"tenant_id": dup1.tenant_id}),
        "cache_by_owner_any_case": _job(db, "github.clear_actions_cache", {"owner": "ACME", "repo": "r"}),
        "cache_ambiguous_owner": _job(db, "github.clear_actions_cache", {"owner": "dup", "repo": "r"}),
        "cache_personal_owner": _job(db, "github.clear_actions_cache", {"owner": "someone", "repo": "r"}),
        "tenant_that_no_longer_exists": _job(db, "github.backfill_repo_events", {"tenant_id": 987654321}),
        "tenant_not_an_int": _job(db, "github.backfill_repo_events", {"tenant_id": True}),
        "not_json": _job(db, "github.clear_actions_cache", "not json at all"),
        "json_but_not_an_object": _job(db, "github.clear_actions_cache", "[1, 2]"),
        "other_job_type": _job(db, "github.something_else", {"tenant_id": acme.tenant_id}),
        "already_attributed": _job(db, "github.clear_actions_cache", {"owner": "acme"}, tenant_id=dup2.tenant_id),
    }

    migration._backfill()

    assert _tenant_of(db, ids["backfill_by_tenant"]) == acme.tenant_id
    assert _tenant_of(db, ids["reconcile_by_tenant"]) == dup1.tenant_id
    assert _tenant_of(db, ids["cache_by_owner_any_case"]) == acme.tenant_id
    # an owner that matches two orgs (case-insensitively) is left alone rather than guessed
    for key in (
        "cache_ambiguous_owner",
        "cache_personal_owner",
        "tenant_that_no_longer_exists",
        "tenant_not_an_int",
        "not_json",
        "json_but_not_an_object",
        "other_job_type",
    ):
        assert _tenant_of(db, ids[key]) is None, key
    # the backfill never rewrites an existing attribution
    assert _tenant_of(db, ids["already_attributed"]) == dup2.tenant_id


def test_backfill_is_a_noop_without_jobs(db, migration):
    migration._backfill()  # nothing to update must not error
