"""Finished jobs must not keep the encrypted GitHub token in jobs.payload (issue #541)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from worker import MAX_RETRIES, RECLAIM_TIMEOUT_MINUTES, _mark_done, _mark_failed, _reclaim_stale_jobs, _requeue_for_retry

PAYLOAD = json.dumps({"owner": "acme", "repo": "api", "token": "v2:secret", "actor": "a@e.com"})


def _insert(conn, created_ids, *, status="processing", retry_count=0, payload=PAYLOAD, updated_at=None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (job_type, payload, status, retry_count, updated_at) "
            "VALUES ('github.clear_actions_cache', %s, %s, %s, COALESCE(%s, NOW())) RETURNING id",
            (payload, status, retry_count, updated_at),
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    created_ids.append(job_id)
    return job_id


def _row(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT status, payload FROM jobs WHERE id = %s", (job_id,))
        status, payload = cur.fetchone()
    return status, payload


def _assert_scrubbed(payload):
    parsed = json.loads(payload)
    assert "token" not in parsed
    assert parsed["owner"] == "acme" and parsed["actor"] == "a@e.com"  # the rest (jobs list reads actor) survives


def test_done_job_loses_its_token(worker_db):
    conn, ids = worker_db
    job_id = _insert(conn, ids)
    assert _mark_done(conn, job_id, {"ok": True}, 0)
    status, payload = _row(conn, job_id)
    assert status == "done"
    _assert_scrubbed(payload)


def test_failed_job_loses_its_token(worker_db):
    conn, ids = worker_db
    job_id = _insert(conn, ids)
    assert _mark_failed(conn, job_id, "boom", 0)
    status, payload = _row(conn, job_id)
    assert status == "failed"
    _assert_scrubbed(payload)


def test_requeued_job_keeps_its_token_for_the_retry(worker_db):
    conn, ids = worker_db
    job_id = _insert(conn, ids)
    assert _requeue_for_retry(conn, job_id, 0, "transient")
    status, payload = _row(conn, job_id)
    assert status == "queued"
    assert json.loads(payload)["token"] == "v2:secret"


def test_job_failed_by_exhausting_retries_loses_its_token(worker_db):
    conn, ids = worker_db
    job_id = _insert(conn, ids, retry_count=MAX_RETRIES)
    assert _requeue_for_retry(conn, job_id, MAX_RETRIES, "still failing")
    status, payload = _row(conn, job_id)
    assert status == "failed"
    _assert_scrubbed(payload)


@pytest.mark.parametrize("retry_count,expected_status,keeps_token", [(0, "queued", True), (MAX_RETRIES, "failed", False)])
def test_reclaim_scrubs_only_when_it_gives_up(worker_db, retry_count, expected_status, keeps_token):
    conn, ids = worker_db
    stale = datetime.now(timezone.utc) - timedelta(minutes=RECLAIM_TIMEOUT_MINUTES + 5)
    job_id = _insert(conn, ids, retry_count=retry_count, updated_at=stale)
    _reclaim_stale_jobs(conn)
    status, payload = _row(conn, job_id)
    assert status == expected_status
    assert ("token" in json.loads(payload)) is keeps_token


def test_scrub_function_handles_unparseable_payloads(worker_db):
    conn, _ids = worker_db
    with conn.cursor() as cur:
        cur.execute("SELECT jobs_scrub_token('not json'), jobs_scrub_token('[1,2]'), jobs_scrub_token('{\"a\": 1}')")
        bad, array, ok = cur.fetchone()
    conn.rollback()
    assert bad == "{}"  # unparseable: nothing to keep
    assert json.loads(array) == [1, 2]  # no top-level key to remove
    assert json.loads(ok) == {"a": 1}
