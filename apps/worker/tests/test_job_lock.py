"""The per-job advisory lock that keeps the reclaim sweep off a job a live worker is still running (#468)."""

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import psycopg
import worker
from config import settings
from worker import (
    _HEARTBEAT_FAILURES_BEFORE_ERROR,
    RECLAIM_TIMEOUT_MINUTES,
    _acquire_job_lock,
    _JobHeartbeat,
    _reclaim_stale_jobs,
    _release_job_lock,
)

_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


class _StopLoop(Exception):
    pass


def _insert_job(conn, created_ids, *, status="processing", updated_at=None, heartbeat_at=None, job_type="github.clear_actions_cache"):
    updated_at = updated_at or datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (job_type, payload, status, updated_at, heartbeat_at) VALUES (%s, '{}', %s, %s, %s) RETURNING id",
            (job_type, status, updated_at, heartbeat_at),
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    created_ids.append(job_id)
    return job_id


def _status(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT status, retry_count FROM jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


def test_a_second_connection_cannot_take_a_held_job_lock(worker_db):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids)
    with psycopg.connect(_DB_URL) as other:
        assert _acquire_job_lock(conn, job_id) is True
        assert _acquire_job_lock(other, job_id) is False
        _release_job_lock(conn, job_id)
        assert _acquire_job_lock(other, job_id) is True
        _release_job_lock(other, job_id)


def test_the_lock_is_per_job_not_global(worker_db):
    conn, created_ids = worker_db
    a, b = _insert_job(conn, created_ids), _insert_job(conn, created_ids)
    with psycopg.connect(_DB_URL) as other:
        assert _acquire_job_lock(conn, a) is True
        assert _acquire_job_lock(other, b) is True
        _release_job_lock(conn, a)
        _release_job_lock(other, b)


def test_reclaim_skips_a_stale_job_whose_lock_a_live_worker_holds(worker_db):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids)  # stale updated_at, no heartbeat: looks crashed
    with psycopg.connect(_DB_URL) as owner:
        assert _acquire_job_lock(owner, job_id)
        _reclaim_stale_jobs(conn)
        assert _status(conn, job_id) == ("processing", 0)  # left alone: a live worker owns it
        _release_job_lock(owner, job_id)

    _reclaim_stale_jobs(conn)
    assert _status(conn, job_id) == ("queued", 1)  # lock gone: reclaimable again


def test_reclaim_still_recovers_a_job_whose_worker_connection_died(worker_db):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids)
    owner = psycopg.connect(_DB_URL)
    assert _acquire_job_lock(owner, job_id)
    owner.close()  # a crashed worker: Postgres drops its session-level lock

    _reclaim_stale_jobs(conn)
    assert _status(conn, job_id) == ("queued", 1)


def test_reclaim_only_skips_the_locked_job_among_several(worker_db):
    conn, created_ids = worker_db
    locked, free = _insert_job(conn, created_ids), _insert_job(conn, created_ids)
    with psycopg.connect(_DB_URL) as owner:
        assert _acquire_job_lock(owner, locked)
        _reclaim_stale_jobs(conn)
        _release_job_lock(owner, locked)
    assert _status(conn, locked)[0] == "processing"
    assert _status(conn, free)[0] == "queued"


def test_release_never_raises_even_on_a_dead_connection(worker_db, caplog):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids)
    dead = psycopg.connect(_DB_URL)
    dead.close()
    with caplog.at_level(logging.WARNING, logger="worker"):
        _release_job_lock(dead, job_id)  # must swallow the error
    assert "could not release the lock" in caplog.text


def test_run_holds_the_job_lock_while_processing_and_releases_it_after(worker_db, monkeypatch):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids, status="queued", updated_at=datetime.now(timezone.utc))
    held_during = {}

    def fake_process(c, jid, *rest):
        with psycopg.connect(_DB_URL) as other:
            held_during["by_other"] = _acquire_job_lock(other, jid)  # False while the worker holds it
            if held_during["by_other"]:
                _release_job_lock(other, jid)

    monkeypatch.setattr(worker, "_read_poll_seconds", lambda: 1)
    monkeypatch.setattr(worker.time, "sleep", MagicMock(side_effect=_StopLoop))
    monkeypatch.setattr(worker, "process_job", fake_process)
    try:
        worker.run()
    except _StopLoop:
        pass

    assert held_during["by_other"] is False
    with psycopg.connect(_DB_URL) as other:  # released afterwards
        assert _acquire_job_lock(other, job_id) is True
        _release_job_lock(other, job_id)


def test_run_does_not_process_a_job_another_live_worker_holds(worker_db, monkeypatch, caplog):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids, status="queued", updated_at=datetime.now(timezone.utc))
    process = MagicMock()
    monkeypatch.setattr(worker, "_read_poll_seconds", lambda: 1)
    monkeypatch.setattr(worker.time, "sleep", MagicMock(side_effect=_StopLoop))
    monkeypatch.setattr(worker, "process_job", process)

    with psycopg.connect(_DB_URL) as owner:
        assert _acquire_job_lock(owner, job_id)
        with caplog.at_level(logging.ERROR, logger="worker"):
            try:
                worker.run()
            except _StopLoop:
                pass
        _release_job_lock(owner, job_id)

    process.assert_not_called()
    assert "locked by another live worker" in caplog.text
    with conn.cursor() as cur:
        cur.execute("SELECT status, retry_count FROM jobs WHERE id = %s", (job_id,))
        assert cur.fetchone() == ("queued", 1)  # handed back for a backed-off retry, not stuck in 'processing' 


def test_run_releases_the_lock_even_when_processing_raises(worker_db, monkeypatch):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids, status="queued", updated_at=datetime.now(timezone.utc))
    monkeypatch.setattr(worker, "_read_poll_seconds", lambda: 1)
    monkeypatch.setattr(worker.time, "sleep", MagicMock(side_effect=_StopLoop))
    monkeypatch.setattr(worker, "process_job", MagicMock(side_effect=RuntimeError("handler blew up")))
    try:
        worker.run()
    except _StopLoop:
        pass
    with psycopg.connect(_DB_URL) as other:
        assert _acquire_job_lock(other, job_id) is True
        _release_job_lock(other, job_id)


# --- heartbeat failure visibility ---------------------------------------------------------------


def test_heartbeat_escalates_to_an_error_every_threshold_failures_and_resets_on_success(monkeypatch, caplog):
    n = _HEARTBEAT_FAILURES_BEFORE_ERROR
    results = iter([False] * (2 * n + 1) + [True] + [False] * n)
    monkeypatch.setattr(worker, "_touch_job_heartbeat", lambda job_id: next(results))
    hb = _JobHeartbeat(123)

    with caplog.at_level(logging.ERROR, logger="worker"):
        for _ in range(n - 1):
            hb._tick()
        assert "heartbeat failed" not in caplog.text  # below the threshold: warnings only
        for _ in range(n + 2):
            hb._tick()
        # Logged at the threshold and again each further threshold failures, so a long outage stays alertable.
        assert caplog.text.count("heartbeat failed") == 2
        hb._tick()  # success resets the streak
        assert hb._consecutive_failures == 0
        for _ in range(n):
            hb._tick()
    assert caplog.text.count("heartbeat failed") == 3  # a new streak escalates again


def test_touch_job_heartbeat_reports_success_and_failure(worker_db, monkeypatch):
    conn, created_ids = worker_db
    job_id = _insert_job(conn, created_ids, updated_at=datetime.now(timezone.utc))
    assert worker._touch_job_heartbeat(job_id) is True

    def boom(*a, **k):
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(psycopg, "connect", boom)
    assert worker._touch_job_heartbeat(job_id) is False
