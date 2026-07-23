"""Integration tests for the reclaim sweep against a real Postgres instance — this is
raw SQL with CASE expressions worth verifying end-to-end rather than only mocking."""

from datetime import datetime, timedelta, timezone

from worker import MAX_RETRIES, RECLAIM_TIMEOUT_MINUTES, _reclaim_stale_jobs


def _insert_job(conn, created_ids, *, status: str, updated_at, retry_count: int = 0, heartbeat_at=None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (job_type, payload, status, retry_count, updated_at, heartbeat_at)
            VALUES ('github.clear_actions_cache', '{}', %s, %s, %s, %s)
            RETURNING id
            """,
            (status, retry_count, updated_at, heartbeat_at),
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    created_ids.append(job_id)
    return job_id


def _fetch(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT status, retry_count, result FROM jobs WHERE id = %s", (job_id,))
        return cur.fetchone()


def test_reclaims_stale_processing_job_back_to_queued(worker_db):
    conn, created_ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    job_id = _insert_job(conn, created_ids, status="processing", updated_at=stale, retry_count=0)

    _reclaim_stale_jobs(conn)

    status, retry_count, _result = _fetch(conn, job_id)
    assert status == "queued"
    assert retry_count == 1


def test_leaves_recently_updated_processing_job_alone(worker_db):
    conn, created_ids = worker_db
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    job_id = _insert_job(conn, created_ids, status="processing", updated_at=recent, retry_count=0)

    _reclaim_stale_jobs(conn)

    status, retry_count, _result = _fetch(conn, job_id)
    assert status == "processing"
    assert retry_count == 0


def test_leaves_a_stale_updated_at_job_alone_if_its_heartbeat_is_still_fresh(worker_db):
    # Regression test for issue #215: a legitimately slow (not crashed) job's updated_at
    # goes stale past RECLAIM_TIMEOUT_MINUTES since it's only set at claim time, but its
    # heartbeat_at is touched every _JOB_HEARTBEAT_INTERVAL_SECONDS by _JobHeartbeat while
    # the handler is actually running -- the reclaim sweep must check heartbeat_at too.
    conn, created_ids = worker_db
    stale_updated_at = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    fresh_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=5)
    job_id = _insert_job(
        conn, created_ids, status="processing", updated_at=stale_updated_at, retry_count=0,
        heartbeat_at=fresh_heartbeat,
    )

    _reclaim_stale_jobs(conn)

    status, retry_count, _result = _fetch(conn, job_id)
    assert status == "processing"
    assert retry_count == 0


def test_reclaims_a_stale_updated_at_job_with_a_stale_heartbeat_too(worker_db):
    conn, created_ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    job_id = _insert_job(
        conn, created_ids, status="processing", updated_at=stale, retry_count=0, heartbeat_at=stale,
    )

    _reclaim_stale_jobs(conn)

    status, retry_count, _result = _fetch(conn, job_id)
    assert status == "queued"
    assert retry_count == 1


def test_reclaims_a_stale_updated_at_job_with_a_null_heartbeat(worker_db):
    # A job claimed before the heartbeat column existed, or whose handler hasn't ticked
    # yet, has heartbeat_at IS NULL -- must still be reclaimed on updated_at staleness
    # alone, same as before this feature existed.
    conn, created_ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    job_id = _insert_job(conn, created_ids, status="processing", updated_at=stale, retry_count=0, heartbeat_at=None)

    _reclaim_stale_jobs(conn)

    status, retry_count, _result = _fetch(conn, job_id)
    assert status == "queued"
    assert retry_count == 1


def test_leaves_queued_and_done_jobs_alone(worker_db):
    conn, created_ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    queued_id = _insert_job(conn, created_ids, status="queued", updated_at=stale)
    done_id = _insert_job(conn, created_ids, status="done", updated_at=stale)

    _reclaim_stale_jobs(conn)

    assert _fetch(conn, queued_id)[0] == "queued"
    assert _fetch(conn, done_id)[0] == "done"


def test_marks_failed_once_reclaim_cap_exceeded(worker_db):
    conn, created_ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    job_id = _insert_job(conn, created_ids, status="processing", updated_at=stale, retry_count=MAX_RETRIES)

    _reclaim_stale_jobs(conn)

    status, retry_count, result = _fetch(conn, job_id)
    assert status == "failed"
    assert retry_count == MAX_RETRIES + 1
    assert "exceeded max reclaim attempts" in result


def test_repeated_reclaims_eventually_fail_a_job_that_keeps_getting_stuck(worker_db):
    """Simulates a job whose worker crashes every time: reclaimed repeatedly, each time
    still ending up stuck in 'processing' (simulated here by re-staling updated_at),
    until retry_count exceeds MAX_RETRIES and it's marked 'failed' instead of looping."""
    conn, created_ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    job_id = _insert_job(conn, created_ids, status="processing", updated_at=stale, retry_count=0)

    for _ in range(MAX_RETRIES):
        _reclaim_stale_jobs(conn)
        status, _retry_count, _result = _fetch(conn, job_id)
        assert status == "queued"
        # Simulate the worker picking it back up (processing) and crashing again.
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'processing', updated_at = %s WHERE id = %s",
                (stale, job_id),
            )
        conn.commit()

    _reclaim_stale_jobs(conn)
    status, retry_count, result = _fetch(conn, job_id)
    assert status == "failed"
    assert retry_count == MAX_RETRIES + 1
    assert "exceeded max reclaim attempts" in result
