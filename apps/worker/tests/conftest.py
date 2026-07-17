"""DB fixture for worker tests that need a real Postgres connection (the reclaim sweep
is raw SQL worth verifying end-to-end, not just mocked). The worker uses psycopg3
directly (not SQLAlchemy), so this mirrors apps/api/tests/conftest.py's real-DB
approach but with psycopg's own connection API.

Unlike apps/api's db fixture, the functions under test (worker._reclaim_stale_jobs,
worker.process_job) call conn.commit() themselves, so a single wrapping transaction
can't be rolled back for isolation. Instead each test creates its own rows and the
fixture deletes anything left over by id.
"""

import psycopg
import pytest

from config import settings

_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture()
def worker_db():
    conn = psycopg.connect(_DB_URL, autocommit=False)
    created_ids: list[int] = []
    try:
        yield conn, created_ids
    finally:
        if created_ids:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM jobs WHERE id = ANY(%s)", (created_ids,))
            conn.commit()
        conn.close()
