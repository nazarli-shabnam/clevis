"""The alert backfill is enqueued with the activity backfill, once, until it has recorded something (#814)."""
from sqlalchemy import text

from src.core.db import Job
from src.repositories import org_repo
from src.services import backfill_service

JOB = "github.backfill_security_alerts"


def _enqueue(db, org):
    backfill_service.enqueue(db, tenant_id=org.tenant_id, account_login="acme", account_type="Organization", token="ghp_x")


def _alert_jobs(db):
    return db.query(Job).filter(Job.job_type == JOB).all()


def test_enqueues_the_alert_backfill_alongside_the_activity_backfill(db):
    org = org_repo.get_or_create(db, github_login="acme")
    _enqueue(db, org)

    (job,) = _alert_jobs(db)
    assert job.tenant_id == org.tenant_id and job.status == "queued"
    assert db.query(Job).filter(Job.job_type == "github.backfill_repo_events").count() == 1
    assert "ghp_x" not in job.payload  # the token is encrypted like the other job's


def test_not_enqueued_again_while_one_is_pending(db):
    org = org_repo.get_or_create(db, github_login="acme")
    _enqueue(db, org)
    _enqueue(db, org)

    assert len(_alert_jobs(db)) == 1


def test_not_enqueued_once_alerts_have_been_synced(db):
    org = org_repo.get_or_create(db, github_login="acme")
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    db.execute(
        text("INSERT INTO security_alert_syncs (tenant_id, repo, kind) VALUES (:t, 'acme/api', 'dependabot')"),
        {"t": org.tenant_id},
    )
    db.commit()

    _enqueue(db, org)

    assert _alert_jobs(db) == []


def test_enqueued_again_once_the_sync_record_has_aged_out(db):
    org = org_repo.get_or_create(db, github_login="acme")
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    db.execute(
        text(
            "INSERT INTO security_alert_syncs (tenant_id, repo, kind, synced_at) "
            "VALUES (:t, 'acme/api', 'dependabot', now() - interval '31 days')"
        ),
        {"t": org.tenant_id},
    )
    db.commit()

    _enqueue(db, org)

    assert len(_alert_jobs(db)) == 1


def test_not_retried_right_after_a_run_that_recorded_nothing_or_failed(db):
    org = org_repo.get_or_create(db, github_login="acme")
    _enqueue(db, org)
    for status in ("done", "failed"):
        db.execute(text("UPDATE jobs SET status = :s WHERE job_type = :j"), {"s": status, "j": JOB})
        db.commit()
        _enqueue(db, org)
        assert len(_alert_jobs(db)) == 1, status


def test_other_tenants_syncs_do_not_suppress_it(db):
    other = org_repo.get_or_create(db, github_login="other-org")
    db.execute(text(f"SET app.tenant_id = {int(other.tenant_id)}"))
    db.execute(
        text("INSERT INTO security_alert_syncs (tenant_id, repo, kind) VALUES (:t, 'other-org/x', 'dependabot')"),
        {"t": other.tenant_id},
    )
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")

    _enqueue(db, org)

    assert len(_alert_jobs(db)) == 1
