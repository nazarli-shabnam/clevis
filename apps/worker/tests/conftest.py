"""Real-Postgres fixture for worker tests.

The code under test commits itself, so isolation is by deleting leftover rows by id
rather than rolling back a wrapping transaction.
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
