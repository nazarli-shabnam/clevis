import json
import time

import httpx
import psycopg

from config import settings

SLEEP = settings.worker_poll_seconds
BASE = settings.github_api_base
# psycopg.connect() expects a plain postgresql:// URI, not the SQLAlchemy +psycopg dialect form
_DB_URL = settings.database_url.replace("postgresql+psycopg://", "postgresql://")


def process_job(conn: psycopg.Connection, job_id: int, payload_raw: str) -> None:
    payload = json.loads(payload_raw)
    owner, repo, token = payload["owner"], payload["repo"], payload["token"]
    params = {k: payload[k] for k in ("key", "ref") if payload.get(k)}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        with httpx.Client(timeout=20) as client:
            resp = client.delete(
                f"{BASE}/repos/{owner}/{repo}/actions/caches",
                headers=headers,
                params=params,
            )
        if resp.status_code >= 300:
            raise RuntimeError(f"GitHub API error: {resp.status_code} {resp.text}")
        result = json.dumps({"ok": True, "status": resp.status_code})
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='done', result=%s, updated_at=NOW() WHERE id=%s",
                (result, job_id),
            )
    except Exception as error:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='failed', result=%s, updated_at=NOW() WHERE id=%s",
                (str(error), job_id),
            )
    conn.commit()


def run() -> None:
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
        except Exception as error:
            print(f"Worker error: {error}", flush=True)

        time.sleep(SLEEP)


if __name__ == "__main__":
    run()
