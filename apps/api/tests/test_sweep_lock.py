"""Tests for the shared advisory-lock helper serializing sweeps' check-then-enqueue across replicas."""

from sqlalchemy import text

from src.services.sweep_lock import try_acquire_sweep_slot


def test_try_acquire_sweep_slot_serializes_concurrent_holders(db, _engine):
    # Two separate connections: advisory locks are reentrant per session, so one can't show contention.
    with _engine.connect() as other_conn:
        other_conn.begin()
        got_other = other_conn.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:job_type), :tenant_id)"),
            {"job_type": "github.backfill_repo_events", "tenant_id": 999999},
        ).scalar()
        assert got_other is True

        assert try_acquire_sweep_slot(db, "github.backfill_repo_events", 999999) is False

        other_conn.commit()  # releases other_conn's advisory lock

    assert try_acquire_sweep_slot(db, "github.backfill_repo_events", 999999) is True


def test_try_acquire_sweep_slot_is_scoped_per_job_type(db, _engine):
    # Different job_types on the same tenant_id must not block each other (hashtext(job_type) separates them).
    with _engine.connect() as other_conn:
        other_conn.begin()
        other_conn.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:job_type), :tenant_id)"),
            {"job_type": "github.backfill_repo_events", "tenant_id": 888888},
        )
        assert try_acquire_sweep_slot(db, "github.reconcile_org_membership", 888888) is True
