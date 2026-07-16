import json
import logging
import time

import httpx
import psycopg
from pydantic import BaseModel, Field, ValidationError

from _crypto import decrypt_job_token
from _sanitize import sanitize_error
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# psycopg.connect() expects plain postgresql://, not the SQLAlchemy +psycopg dialect prefix
_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")

# Shared cap on jobs.retry_count, incremented by both the reclaim sweep (a worker
# crashed mid-job) and a transient-failure requeue in process_job — either path marks
# the job 'failed' once exceeded, so a job can't retry forever regardless of cause.
MAX_RETRIES = 5
# A job left in 'processing' longer than this almost certainly had its worker crash or
# get killed mid-job (see _reclaim_stale_jobs) rather than still being genuinely in flight.
RECLAIM_TIMEOUT_MINUTES = 30


def _read_app_config(key: str, default: str) -> str:
    """Read a single value from app_config. Falls back to default on any error."""
    try:
        with psycopg.connect(_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
                row = cur.fetchone()
        return row[0] if row else default
    except Exception as exc:
        log.warning("Could not read app_config[%r]: %s; using default %r", key, exc, default)
        return default


def _read_poll_seconds() -> int:
    """Read worker_poll_seconds, clamped to a minimum of 1. Falls back to 5 on a
    malformed value so a bad config row can never crash or busy-loop the worker."""
    raw = _read_app_config("worker_poll_seconds", "5")
    try:
        return max(1, int(raw))
    except ValueError:
        log.warning("worker_poll_seconds %r is not an integer; using 5", raw)
        return 5


class ClearActionsCachePayload(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    token: str = Field(min_length=1)
    key: str | None = None
    ref: str | None = None


def _mark_done(conn: psycopg.Connection, job_id: int, result: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='done', result=%s, updated_at=NOW() WHERE id=%s",
            (json.dumps(result), job_id),
        )
    conn.commit()


def _mark_failed(conn: psycopg.Connection, job_id: int, error_text: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='failed', result=%s, updated_at=NOW() WHERE id=%s",
            (error_text, job_id),
        )
    conn.commit()


def _requeue_for_retry(conn: psycopg.Connection, job_id: int, retry_count: int, error_text: str) -> None:
    new_count = retry_count + 1
    if new_count > MAX_RETRIES:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='failed', retry_count=%s, result=%s, updated_at=NOW() WHERE id=%s",
                (new_count, f"exceeded max retry attempts ({MAX_RETRIES}): {error_text}", job_id),
            )
    else:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='queued', retry_count=%s, result=%s, updated_at=NOW() WHERE id=%s",
                (new_count, error_text, job_id),
            )
    conn.commit()


def process_job(conn: psycopg.Connection, job_id: int, payload_raw: str, retry_count: int = 0) -> None:
    try:
        payload = ClearActionsCachePayload.model_validate_json(payload_raw)
    except ValidationError as error:
        log.error("job %d has an invalid payload: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error))
        return

    try:
        token = decrypt_job_token(payload.token, settings.job_secret_key.get_secret_value())
    except Exception as error:
        log.error("job %d failed to decrypt its token: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error))
        return

    base = settings.github_api_base
    params = {k: v for k, v in (("key", payload.key), ("ref", payload.ref)) if v}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        try:
            with httpx.Client(timeout=20) as client:
                resp = client.delete(
                    f"{base}/repos/{payload.owner}/{payload.repo}/actions/caches",
                    headers=headers,
                    params=params,
                )
        except httpx.RequestError as error:
            log.warning("job %d hit a network error (attempt %d): %s", job_id, retry_count + 1, error)
            _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
            return

        if resp.status_code >= 500:
            # 5xx is presumed transient (GitHub-side issue) — worth retrying, unlike 4xx.
            log.warning("job %d got a %d from GitHub (attempt %d)", job_id, resp.status_code, retry_count + 1)
            _requeue_for_retry(conn, job_id, retry_count, f"GitHub API error: {resp.status_code}")
            return

        if resp.status_code >= 300:
            log.error("job %d failed: GitHub API error %d", job_id, resp.status_code)
            _mark_failed(conn, job_id, f"GitHub API error: {resp.status_code}")
            return

        _mark_done(conn, job_id, {"ok": True, "status": resp.status_code})
        log.info("job %d done", job_id)
    except Exception as error:
        # Safety net for anything not handled above (e.g. a bug in this function, or an
        # unanticipated exception type) — without this, the job would stay 'processing'
        # until the reclaim sweep picks it up, up to RECLAIM_TIMEOUT_MINUTES later,
        # instead of failing/retrying immediately.
        log.error("job %d hit an unexpected error: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error))


def _reclaim_stale_jobs(conn: psycopg.Connection) -> None:
    """Reset jobs stuck in 'processing' past RECLAIM_TIMEOUT_MINUTES back to 'queued' —
    the worker that claimed them almost certainly crashed or was killed mid-job, and the
    poll query only ever selects 'queued' rows, so without this such a job is stuck
    forever. Shares retry_count/MAX_RETRIES with process_job's transient-failure retry so
    a job that repeatedly crashes its worker eventually gets marked 'failed' instead of
    looping indefinitely."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN retry_count + 1 > %(max_retries)s THEN 'failed' ELSE 'queued' END,
                retry_count = retry_count + 1,
                result = CASE WHEN retry_count + 1 > %(max_retries)s THEN %(exceeded_message)s ELSE result END,
                updated_at = NOW()
            WHERE status = 'processing'
              AND updated_at < NOW() - make_interval(mins => %(timeout_minutes)s)
            RETURNING id, status
            """,
            {
                "max_retries": MAX_RETRIES,
                "exceeded_message": f"exceeded max reclaim attempts ({MAX_RETRIES})",
                "timeout_minutes": RECLAIM_TIMEOUT_MINUTES,
            },
        )
        reclaimed = cur.fetchall()
    conn.commit()
    for job_id, status in reclaimed:
        log.warning("reclaimed stale job %d -> %s", job_id, status)


def run() -> None:
    poll_seconds = _read_poll_seconds()
    log.info("worker started, polling every %ds", poll_seconds)
    while True:
        # Re-read poll interval each cycle so changes in settings take effect without restart
        poll_seconds = _read_poll_seconds()
        try:
            with psycopg.connect(_DB_URL) as conn:
                _reclaim_stale_jobs(conn)

                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE jobs SET status = 'processing', updated_at = NOW()
                        WHERE id = (
                            SELECT id FROM jobs
                            WHERE status = 'queued'
                              AND job_type = 'github.clear_actions_cache'
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        RETURNING id, payload, retry_count
                    """)
                    row = cur.fetchone()

                if row:
                    conn.commit()
                    process_job(conn, *row)
        except psycopg.OperationalError:
            log.error("database connection failed, retrying in %ds", poll_seconds)
        except Exception as error:
            log.error("worker poll error: %s", type(error).__name__)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    run()
