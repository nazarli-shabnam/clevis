import json
import logging
import time
from collections.abc import Callable

import httpx
import psycopg

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

JOB_TYPE_CLEAR_ACTIONS_CACHE = "github.clear_actions_cache"


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


def _mark_failed(conn: psycopg.Connection, job_id: int, reason: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='failed', result=%s, updated_at=NOW() WHERE id=%s",
            (sanitize_error(reason), job_id),
        )
    conn.commit()


def _process_clear_actions_cache(conn: psycopg.Connection, job_id: int, payload_raw: str) -> None:
    base = settings.github_api_base
    payload = json.loads(payload_raw)
    owner, repo = payload["owner"], payload["repo"]
    token = decrypt_job_token(payload["token"], settings.job_secret_key.get_secret_value())
    params = {k: payload[k] for k in ("key", "ref") if payload.get(k)}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        resp = client.delete(
            f"{base}/repos/{owner}/{repo}/actions/caches",
            headers=headers,
            params=params,
        )
    if resp.status_code >= 300:
        raise RuntimeError(f"GitHub API error: {resp.status_code}")
    result = json.dumps({"ok": True, "status": resp.status_code})
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='done', result=%s, updated_at=NOW() WHERE id=%s",
            (result, job_id),
        )
    log.info("job %d done", job_id)
    conn.commit()


_JOB_HANDLERS: dict[str, Callable[[psycopg.Connection, int, str], None]] = {
    JOB_TYPE_CLEAR_ACTIONS_CACHE: _process_clear_actions_cache,
}


def process_job(conn: psycopg.Connection, job_id: int, job_type: str, payload_raw: str) -> None:
    handler = _JOB_HANDLERS.get(job_type)
    if handler is None:
        log.error("job %d has unsupported job_type %r", job_id, job_type)
        _mark_failed(conn, job_id, f"Unsupported job_type: {job_type}")
        return
    try:
        handler(conn, job_id, payload_raw)
    except Exception as error:
        log.error("job %d failed: %s", job_id, error)
        _mark_failed(conn, job_id, str(error))


def run() -> None:
    poll_seconds = _read_poll_seconds()
    log.info("worker started, polling every %ds", poll_seconds)
    while True:
        # Re-read poll interval each cycle so changes in settings take effect without restart
        poll_seconds = _read_poll_seconds()
        try:
            with psycopg.connect(_DB_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE jobs SET status = 'processing', updated_at = NOW()
                        WHERE id = (
                            SELECT id FROM jobs
                            WHERE status = 'queued'
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        RETURNING id, job_type, payload
                    """)
                    row = cur.fetchone()

                if row:
                    conn.commit()
                    process_job(conn, row[0], row[1], row[2])
        except psycopg.OperationalError:
            log.error("database connection failed, retrying in %ds", poll_seconds)
        except Exception as error:
            log.error("worker poll error: %s", type(error).__name__)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    run()
