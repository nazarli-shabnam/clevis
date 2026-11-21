"""Every table the worker's SQL touches must be reachable by the ``clevis_worker`` role.

The rest of the suite runs as a privileged role, so a worker query on a table (or a verb, or a serial
sequence) the role was never granted only fails for a self-hoster who enabled ``WORKER_DB_PASSWORD``
(issue #1164). This test reads the SQL out of ``apps/worker/src`` and asks Postgres whether the role it
is connected as may run it. It only means something as ``clevis_worker``, so it skips otherwise; the
``worker-grants`` CI job creates the role with ``docker/postgres-init/01-create-worker-role.sh``,
migrates, and runs this file with ``DATABASE_URL`` pointing at it.
"""

import re
from pathlib import Path

import psycopg
import pytest

from config import settings

_SRC = Path(__file__).resolve().parents[1] / "src"
_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")

# Upper-case keywords only: the worker's SQL is written that way, and prose in comments is not.
_VERBS = {
    "insert": re.compile(r"\bINSERT\s+INTO\s+([a-z_][a-z0-9_]*)"),
    "update": re.compile(r"\bUPDATE\s+([a-z_][a-z0-9_]*)\s+SET\b"),
    "delete": re.compile(r"\bDELETE\s+FROM\s+([a-z_][a-z0-9_]*)"),
    "select": re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)"),
}


def _sql_uses(tables: set[str]) -> dict[tuple[str, str], str]:
    """``(table, privilege) -> file`` for every statement in the worker source that names a real table."""
    uses: dict[tuple[str, str], str] = {}
    for path in sorted(_SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for privilege, pattern in _VERBS.items():
            for name in pattern.findall(text):
                if name in tables:  # drops CTE names, catalogs and words that are not tables
                    uses.setdefault((name, privilege), path.name)
    return uses


@pytest.fixture(scope="module")
def conn():
    with psycopg.connect(_DB_URL) as c:
        if c.execute("SELECT current_user").fetchone()[0] != "clevis_worker":
            pytest.skip("not connected as clevis_worker (run by the worker-grants CI job)")
        yield c


def _may(conn, table: str, privilege: str) -> bool:
    # Column-level grants (users, memberships, api_tokens, orgs) count: the role may touch *some* column.
    return conn.execute(
        "SELECT has_table_privilege(%s, %s) OR has_any_column_privilege(%s, %s)",
        (table, privilege.upper(), table, privilege.upper()),
    ).fetchone()[0]


def test_the_worker_role_may_run_every_statement_in_its_source(conn):
    tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    uses = _sql_uses(tables)
    assert uses, "found no SQL in apps/worker/src: the scan itself is broken"
    missing = sorted(f"{priv.upper()} on {table} ({src})" for (table, priv), src in uses.items() if not _may(conn, table, priv))
    assert not missing, "clevis_worker lacks privileges the worker code needs: " + "; ".join(missing)


def test_the_worker_role_may_use_the_sequence_of_every_table_it_inserts_into(conn):
    tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    missing = []
    for (table, privilege), src in _sql_uses(tables).items():
        if privilege != "insert":
            continue
        sequences = conn.execute(
            "SELECT pg_get_serial_sequence(%s, attname) FROM pg_attribute "
            "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped",
            (table, table),
        ).fetchall()
        for (seq,) in sequences:
            if seq and not conn.execute("SELECT has_sequence_privilege(%s, 'USAGE')", (seq,)).fetchone()[0]:
                missing.append(f"USAGE on {seq} ({src})")
    assert not missing, "clevis_worker cannot draw ids for: " + "; ".join(sorted(missing))
