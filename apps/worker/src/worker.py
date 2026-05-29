import json
import logging
import time

import httpx
import psycopg

from _crypto import decrypt_job_token
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# psycopg.connect() expects plain postgresql://, not the SQLAlchemy +psycopg dialect prefix
_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


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


def process_job(conn: psycopg.Connection, job_id: int, payload_raw: str) -> None:
    base = _read_app_config("github_api_base", "https://api.github.com")
    try:
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
    except Exception as error:
        log.error("job %d failed: %s", job_id, error)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='failed', result=%s, updated_at=NOW() WHERE id=%s",
                (str(error), job_id),
            )
    conn.commit()


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
