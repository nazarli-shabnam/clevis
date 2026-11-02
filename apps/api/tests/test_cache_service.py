"""cache_service.clear: the queued job and its audit row are one transaction."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.core.db import AuditLog, Job, User, set_session_tenant
from src.repositories import audit_repo, tenant_repo
from src.schemas.cache import CacheClearInput
from src.services import cache_service


@pytest.fixture(autouse=True)
def tenant_id(db):
    """audit_logs is tenant-scoped under RLS, so every test runs inside a real tenant."""
    user = User(email="cache-svc@example.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    tid = tenant_repo.ensure_personal_tenant(db, user.id).id
    set_session_tenant(db, tid)
    return tid


def _clear(db, tenant_id, **overrides):
    payload = CacheClearInput(dry_run=False, key="build-cache", ref="refs/heads/main")
    return cache_service.clear(db, "acme", "api", payload, actor="admin@example.com", token="ghp_x", tenant_id=tenant_id, **overrides)


def _clear_jobs(db):
    return db.query(Job).filter(Job.job_type == "github.clear_actions_cache").all()


def _queued_audit_rows(db):
    return db.query(AuditLog).filter(AuditLog.action == "cache.clear.queued").all()


def test_clear_enqueues_the_job_and_audits_it(db, tenant_id):
    result = _clear(db, tenant_id)

    assert result["queued"] is True
    jobs = _clear_jobs(db)
    assert [j.id for j in jobs] == [result["job_id"]]
    assert jobs[0].status == "queued"
    rows = _queued_audit_rows(db)
    assert len(rows) == 1
    assert rows[0].target == "acme/api"
    assert json.loads(rows[0].payload)["job_id"] == result["job_id"]


def test_clear_commits_once_for_the_job_and_the_audit_row(db, tenant_id):
    with patch.object(db, "commit", wraps=db.commit) as commit:
        _clear(db, tenant_id)

    assert commit.call_count == 1


def test_clear_does_not_leave_a_runnable_job_when_the_audit_write_fails(db, tenant_id):
    """The worker polls for queued jobs, so a job committed before its audit row would run unaudited."""
    with patch.object(audit_repo, "write", side_effect=RuntimeError("audit insert failed")):
        with pytest.raises(RuntimeError):
            _clear(db, tenant_id)
    db.rollback()  # what get_db does when the request fails

    assert _clear_jobs(db) == []
    assert _queued_audit_rows(db) == []


def test_dry_run_still_writes_only_an_audit_row(db, tenant_id):
    result = cache_service.clear(
        db, "acme", "api", CacheClearInput(dry_run=True), actor="admin@example.com", token="", tenant_id=tenant_id
    )

    assert result["dry_run"] is True
    assert _clear_jobs(db) == []
    assert db.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'cache.clear.dry_run'")).scalar() == 1
