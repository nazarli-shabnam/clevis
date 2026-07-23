"""Tests for _JobHeartbeat / _touch_job_heartbeat (issue #215)."""

import time
from datetime import datetime, timedelta, timezone

import psycopg
import worker
from worker import _JobHeartbeat, _touch_job_heartbeat


def _insert_processing_job(conn, created_ids) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (job_type, payload, status, updated_at)
            VALUES ('github.clear_actions_cache', '{}', 'processing', NOW())
            RETURNING id
            """
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    created_ids.append(job_id)
    return job_id


def _heartbeat_at(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT heartbeat_at FROM jobs WHERE id = %s", (job_id,))
        return cur.fetchone()[0]


def test_touch_job_heartbeat_sets_heartbeat_at_on_a_processing_job(worker_db):
    conn, created_ids = worker_db
    job_id = _insert_processing_job(conn, created_ids)

    _touch_job_heartbeat(job_id)

    heartbeat = _heartbeat_at(conn, job_id)
    assert heartbeat is not None
    assert heartbeat > datetime.now(timezone.utc) - timedelta(seconds=10)


def test_touch_job_heartbeat_is_a_noop_for_a_job_that_is_no_longer_processing(worker_db):
    conn, created_ids = worker_db
    job_id = _insert_processing_job(conn, created_ids)
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (job_id,))
    conn.commit()

    _touch_job_heartbeat(job_id)

    assert _heartbeat_at(conn, job_id) is None


def test_touch_job_heartbeat_swallows_a_db_connection_failure(monkeypatch, tmp_path):
    # The DB heartbeat write is non-fatal by design (see the comment in
    # _touch_job_heartbeat) -- a DB hiccup mid-job must not crash the job handler. Also
    # verifies the container-level file heartbeat still gets touched even when the DB
    # write fails, since it sits outside the try/except.
    def boom(*args, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(psycopg, "connect", boom)
    heartbeat_file = tmp_path / "worker_heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", heartbeat_file)

    _touch_job_heartbeat(999)  # must not raise

    assert heartbeat_file.exists()


def test_touch_job_heartbeat_also_refreshes_the_container_heartbeat_file(worker_db, tmp_path, monkeypatch):
    # Regression test: run()'s poll loop only touches HEARTBEAT_FILE once per iteration,
    # before a job is even claimed -- without this, a job handler running past the
    # healthcheck's 60s staleness threshold gets the worker marked unhealthy mid-job.
    heartbeat_file = tmp_path / "worker_heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", heartbeat_file)
    conn, created_ids = worker_db
    job_id = _insert_processing_job(conn, created_ids)

    _touch_job_heartbeat(job_id)

    assert heartbeat_file.exists()


def test_job_heartbeat_ticks_repeatedly_while_the_context_is_open(worker_db, monkeypatch):
    # Short interval so the test doesn't take _JOB_HEARTBEAT_INTERVAL_SECONDS' real 10s.
    monkeypatch.setattr(worker, "_JOB_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    conn, created_ids = worker_db
    job_id = _insert_processing_job(conn, created_ids)

    with _JobHeartbeat(job_id):
        first_tick = _heartbeat_at(conn, job_id)
        assert first_tick is not None  # touched immediately on __enter__, no wait
        time.sleep(0.2)
        second_tick = _heartbeat_at(conn, job_id)

    assert second_tick > first_tick


def test_job_heartbeat_stops_ticking_after_the_context_exits(worker_db, monkeypatch):
    monkeypatch.setattr(worker, "_JOB_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    conn, created_ids = worker_db
    job_id = _insert_processing_job(conn, created_ids)

    with _JobHeartbeat(job_id):
        time.sleep(0.1)
    after_exit = _heartbeat_at(conn, job_id)
    time.sleep(0.2)
    assert _heartbeat_at(conn, job_id) == after_exit
