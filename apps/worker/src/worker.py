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

SLEEP = settings.worker_poll_seconds
BASE = settings.github_api_base
# psycopg.connect() expects plain postgresql://, not the SQLAlchemy +psycopg dialect prefix
_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


def process_job(conn: psycopg.Connection, job_id: int, payload_raw: str) -> None:
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
                f"{BASE}/repos/{owner}/{repo}/actions/caches",
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
    log.info("worker started, polling every %ds", SLEEP)
    while True:
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
            log.error("database connection failed, retrying in %ds", SLEEP)
        except Exception as error:
            log.error("worker poll error: %s", type(error).__name__)

        time.sleep(SLEEP)


if __name__ == "__main__":
    run()
