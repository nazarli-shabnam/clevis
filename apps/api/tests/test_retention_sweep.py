"""Tests for the retention sweep: what it deletes, what it must keep, and its limits."""

import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.core.db import Job, RepoEvent, RepoEventDailyCount, User, WebhookDelivery, set_session_tenant
from src.repositories import tenant_repo
from src.services import retention_sweep
from src.services.retention_sweep import run_retention_sweep

_NOW = datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return _NOW - timedelta(days=days)


def _config(**values):
    """A get_config stand-in: the given values, else the caller's default."""
    return lambda key, default="": values.get(key, default)


def _run(db, **values):
    with patch.object(retention_sweep, "get_config", _config(**values)):
        run_retention_sweep(db)


def _tenant(db, email: str) -> int:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return tenant_repo.ensure_personal_tenant(db, user.id).id


def _delivery(db, *, status: str, age_days: float, name: str) -> None:
    row = WebhookDelivery(delivery_id=name, event_type="push", payload=b"{}", status=status)
    db.add(row)
    db.commit()
    db.execute(
        text("UPDATE webhook_deliveries SET received_at = :ts WHERE id = :id"),
        {"ts": _ago(age_days), "id": row.id},
    )
    db.commit()


def _job(db, *, status: str, age_days: float, name: str) -> None:
    row = Job(job_type=name, payload="{}", status=status)
    db.add(row)
    db.commit()
    db.execute(text("UPDATE jobs SET updated_at = :ts WHERE id = :id"), {"ts": _ago(age_days), "id": row.id})
    db.commit()


def _event(db, *, tenant_id: int, age_days: float, name: str) -> None:
    set_session_tenant(db, tenant_id)  # repo_events is RLS-forced
    db.add(
        RepoEvent(
            tenant_id=tenant_id, delivery_id=name, event_type="push", actor="alice", actor_avatar="",
            repo="api", summary="pushed", occurred_at=_ago(age_days),
        )
    )
    db.commit()


def _left(db, model, column, names) -> set[str]:
    return {value for (value,) in db.query(column).filter(column.in_(names)).all()}


# ── webhook_deliveries ───────────────────────────────────────────────────────

def test_finished_webhook_deliveries_past_the_window_are_deleted(db):
    for status in ("processed", "dropped", "queue_abandoned"):
        _delivery(db, status=status, age_days=45, name=f"old-{status}")
    _run(db)

    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["old-processed", "old-dropped", "old-queue_abandoned"]) == set()


def test_webhook_deliveries_still_in_the_pipeline_are_never_deleted(db):
    _delivery(db, status="queued", age_days=400, name="old-queued")
    _delivery(db, status="queue_failed", age_days=400, name="old-queue-failed")
    _run(db)

    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["old-queued", "old-queue-failed"]) == {
        "old-queued",
        "old-queue-failed",
    }


def test_recent_finished_webhook_deliveries_are_kept(db):
    _delivery(db, status="processed", age_days=5, name="recent-processed")
    _run(db)

    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["recent-processed"]) == {"recent-processed"}


# ── jobs ─────────────────────────────────────────────────────────────────────

def test_finished_jobs_past_the_window_are_deleted_and_active_ones_kept(db):
    _job(db, status="done", age_days=45, name="ret-done-old")
    _job(db, status="failed", age_days=45, name="ret-failed-old")
    _job(db, status="queued", age_days=45, name="ret-queued-old")
    _job(db, status="processing", age_days=45, name="ret-processing-old")
    _job(db, status="done", age_days=3, name="ret-done-recent")
    _run(db)

    kept = _left(db, Job, Job.job_type, ["ret-done-old", "ret-failed-old", "ret-queued-old", "ret-processing-old", "ret-done-recent"])
    assert kept == {"ret-queued-old", "ret-processing-old", "ret-done-recent"}


def test_jobs_window_cannot_be_set_below_the_failed_job_marker_window(db):
    """has_blocking_job treats a job that failed within the sweep stale window (up to 7 days) as 'already tried'."""
    _job(db, status="failed", age_days=5, name="ret-failed-5d")
    _job(db, status="failed", age_days=9, name="ret-failed-9d")

    _run(db, retention_jobs_days="1")

    assert _left(db, Job, Job.job_type, ["ret-failed-5d", "ret-failed-9d"]) == {"ret-failed-5d"}


# ── repo_events ──────────────────────────────────────────────────────────────

def test_old_repo_events_are_deleted_per_tenant_and_the_rollup_is_kept(db):
    tenant_a, tenant_b = _tenant(db, "ret-a@example.com"), _tenant(db, "ret-b@example.com")
    for tenant_id, label in ((tenant_a, "a"), (tenant_b, "b")):
        _event(db, tenant_id=tenant_id, age_days=400, name=f"ev-{label}-old")
        _event(db, tenant_id=tenant_id, age_days=10, name=f"ev-{label}-recent")
    set_session_tenant(db, tenant_a)
    db.add(RepoEventDailyCount(tenant_id=tenant_a, repo="api", event_type="push", day=date(2025, 1, 1), count=7))
    db.commit()

    _run(db)

    # repo_events is RLS-scoped, so each tenant's rows are read under its own context.
    for tenant_id, label in ((tenant_a, "a"), (tenant_b, "b")):
        set_session_tenant(db, tenant_id)
        names = [f"ev-{label}-old", f"ev-{label}-recent"]
        assert _left(db, RepoEvent, RepoEvent.delivery_id, names) == {f"ev-{label}-recent"}
    set_session_tenant(db, tenant_a)
    assert db.query(RepoEventDailyCount).filter_by(tenant_id=tenant_a).one().count == 7


def test_repo_events_window_cannot_be_set_below_githubs_event_window(db):
    """Backfill re-imports GitHub's last 90 days, so a webhook-stored row deleted sooner would be counted twice."""
    tenant_id = _tenant(db, "ret-floor@example.com")
    _event(db, tenant_id=tenant_id, age_days=95, name="ev-95d")
    _event(db, tenant_id=tenant_id, age_days=120, name="ev-120d")

    _run(db, retention_repo_events_days="30")

    assert _left(db, RepoEvent, RepoEvent.delivery_id, ["ev-95d", "ev-120d"]) == {"ev-95d"}


# ── configuration ────────────────────────────────────────────────────────────

def test_zero_switches_a_purge_off(db):
    _delivery(db, status="processed", age_days=400, name="zero-delivery")
    _job(db, status="done", age_days=400, name="ret-zero-job")
    tenant_id = _tenant(db, "ret-zero@example.com")
    _event(db, tenant_id=tenant_id, age_days=900, name="zero-event")

    _run(
        db,
        retention_webhook_deliveries_days="0",
        retention_jobs_days="0",
        retention_repo_events_days="0",
    )

    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["zero-delivery"]) == {"zero-delivery"}
    assert _left(db, Job, Job.job_type, ["ret-zero-job"]) == {"ret-zero-job"}
    assert _left(db, RepoEvent, RepoEvent.delivery_id, ["zero-event"]) == {"zero-event"}


def test_one_purge_can_be_off_while_the_others_run(db):
    _delivery(db, status="processed", age_days=400, name="mixed-delivery")
    _job(db, status="done", age_days=400, name="ret-mixed-job")

    _run(db, retention_jobs_days="0")

    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["mixed-delivery"]) == set()
    assert _left(db, Job, Job.job_type, ["ret-mixed-job"]) == {"ret-mixed-job"}


@pytest.mark.parametrize("bad", ["abc", "-5", ""])
def test_an_invalid_value_falls_back_to_the_default_window(db, bad, caplog):
    _delivery(db, status="processed", age_days=45, name="bad-old")  # past the 30-day default
    _delivery(db, status="processed", age_days=10, name="bad-recent")  # inside it

    with caplog.at_level(logging.WARNING):
        _run(db, retention_webhook_deliveries_days=bad)

    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["bad-old", "bad-recent"]) == {"bad-recent"}
    assert any("retention_webhook_deliveries_days" in r.message for r in caplog.records)


def test_a_missing_delete_privilege_is_reported_clearly_and_does_not_stop_the_others(db, caplog):
    from psycopg.errors import InsufficientPrivilege
    from sqlalchemy.exc import ProgrammingError

    _job(db, status="done", age_days=45, name="ret-priv-job")
    denied = ProgrammingError("DELETE ...", {}, InsufficientPrivilege("permission denied for table repo_events"))

    with patch.object(retention_sweep, "_purge_repo_events", side_effect=denied):
        with caplog.at_level(logging.ERROR):
            _run(db)

    assert _left(db, Job, Job.job_type, ["ret-priv-job"]) == set()
    messages = [r.message for r in caplog.records]
    assert any("retention_repo_events_days skipped" in m and "permission denied for table repo_events" in m for m in messages)


def test_the_window_is_capped_at_ten_years():
    with patch.object(retention_sweep, "get_config", _config(retention_jobs_days="999999")):
        assert retention_sweep.retention_days(retention_sweep.JOBS) == 3650


# ── bounded work ─────────────────────────────────────────────────────────────

def test_a_tick_stops_after_the_batch_cap_and_the_next_tick_continues(db, caplog):
    for i in range(5):
        _delivery(db, status="processed", age_days=45, name=f"batch-{i}")

    with patch.object(retention_sweep, "BATCH_SIZE", 2), patch.object(retention_sweep, "MAX_BATCHES", 2):
        with caplog.at_level(logging.INFO):
            _run(db)
        assert any("batch cap reached" in r.message for r in caplog.records)
        after_first = len(_left(db, WebhookDelivery, WebhookDelivery.delivery_id, [f"batch-{i}" for i in range(5)]))
        _run(db)
        after_second = len(_left(db, WebhookDelivery, WebhookDelivery.delivery_id, [f"batch-{i}" for i in range(5)]))

    assert after_first == 1  # 2 batches x 2 rows deleted, one row left for the next tick
    assert after_second == 0


def test_a_failing_purge_does_not_stop_the_others(db, caplog):
    _job(db, status="done", age_days=45, name="ret-isolated-job")
    _delivery(db, status="processed", age_days=45, name="isolated-delivery")

    with patch.object(retention_sweep, "_purge_deliveries", side_effect=RuntimeError("lock timeout")):
        with caplog.at_level(logging.ERROR):
            _run(db)

    assert _left(db, Job, Job.job_type, ["ret-isolated-job"]) == set()
    assert _left(db, WebhookDelivery, WebhookDelivery.delivery_id, ["isolated-delivery"]) == {"isolated-delivery"}
    assert any("retention purge failed" in r.message for r in caplog.records)


def test_a_requested_shutdown_stops_further_delete_batches(db):
    for i in range(3):
        _delivery(db, status="processed", age_days=45, name=f"stop-{i}")

    retention_sweep.stop_requested.set()
    try:
        _run(db)
    finally:
        retention_sweep.stop_requested.clear()

    assert len(_left(db, WebhookDelivery, WebhookDelivery.delivery_id, [f"stop-{i}" for i in range(3)])) == 3
