import json
import logging
import time

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

_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")
_PROCESSING_STALE_MINUTES = 30
_REQUIRED_PAYLOAD_KEYS = ("owner", "repo", "token")


def _read_app_config(key: str, default: str) -> str:
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
    raw = _read_app_config("worker_poll_seconds", "5")
    try:
        return max(1, int(raw))
    except ValueError:
        log.warning("worker_poll_seconds %r is not an integer; using 5", raw)
        return 5


def _reclaim_stale_jobs(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs SET status = 'queued', updated_at = NOW()
            WHERE status = 'processing'
              AND updated_at < NOW() - make_interval(mins => %s)
            """,
            (_PROCESSING_STALE_MINUTES,),
        )
    conn.commit()


def _validate_payload(payload: dict) -> None:
    for key in _REQUIRED_PAYLOAD_KEYS:
        value = payload.get(key)
        if value is None or value == "":
            raise ValueError(f"Missing required payload field: {key}")


def process_job(conn: psycopg.Connection, job_id: int, payload_raw: str) -> None:
    base = settings.github_api_base
    try:
        payload = json.loads(payload_raw)
        if not isinstance(payload, dict):
            raise ValueError("Job payload must be a JSON object")
        _validate_payload(payload)
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
    except Exception as error:
        log.error("job %d failed: %s", job_id, error)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='failed', result=%s, updated_at=NOW() WHERE id=%s",
                (sanitize_error(error), job_id),
            )
    conn.commit()


def run() -> None:
    poll_seconds = _read_poll_seconds()
    log.info("worker started, polling every %ds", poll_seconds)
    while True:
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
                        RETURNING id, payload
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
