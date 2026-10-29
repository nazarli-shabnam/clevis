"""Tests for org_membership_store's tenant advisory lock: serializes connections, reentrant per connection."""

import threading

import psycopg
import pytest

from config import settings
from org_membership_store import acquire_tenant_lock, release_tenant_lock, try_acquire_tenant_lock

_DB_URL = settings.database_url.get_secret_value().replace("postgresql+psycopg://", "postgresql://")


@pytest.fixture()
def second_conn():
    conn = psycopg.connect(_DB_URL, autocommit=False)
    try:
        yield conn
    finally:
        conn.close()


def test_acquire_tenant_lock_serializes_across_connections(worker_db, second_conn):
    conn_a, _ = worker_db
    tenant_id = 999_000_001  # arbitrary key -- doesn't need to reference a real tenants row

    acquire_tenant_lock(conn_a, tenant_id)
    acquired_by_b = threading.Event()

    def try_acquire_from_b():
        acquire_tenant_lock(second_conn, tenant_id)
        acquired_by_b.set()

    t = threading.Thread(target=try_acquire_from_b)
    t.start()
    try:
        # conn_a still holds the lock, so conn_b's acquire must still be blocked here.
        assert not acquired_by_b.wait(timeout=0.5)
    finally:
        release_tenant_lock(conn_a, tenant_id)
    # Once conn_a releases, conn_b's blocked acquire should complete promptly.
    assert acquired_by_b.wait(timeout=2.0)
    t.join(timeout=2.0)
    release_tenant_lock(second_conn, tenant_id)


def test_acquire_tenant_lock_is_reentrant_on_same_connection(worker_db):
    """Session-level advisory locks are reentrant; the reconcile handler relies on that."""
    conn, _ = worker_db
    tenant_id = 999_000_002

    acquire_tenant_lock(conn, tenant_id)
    acquire_tenant_lock(conn, tenant_id)  # would hang here if this weren't reentrant
    release_tenant_lock(conn, tenant_id)
    release_tenant_lock(conn, tenant_id)


def test_acquire_tenant_lock_returns_the_database_clock(worker_db):
    from datetime import datetime, timedelta, timezone

    conn, _ = worker_db
    tenant_id = 999_000_003

    before = datetime.now(timezone.utc) - timedelta(seconds=30)  # generous: the DB host's clock may differ a little
    locked_at = acquire_tenant_lock(conn, tenant_id)
    release_tenant_lock(conn, tenant_id)

    assert locked_at.tzinfo is not None and before < locked_at < before + timedelta(seconds=90)
    with conn.cursor() as cur:
        cur.execute("SELECT clock_timestamp()")
        assert cur.fetchone()[0] >= locked_at


def test_try_acquire_tenant_lock_returns_none_while_held_elsewhere_then_succeeds(worker_db, second_conn):
    conn_a, _ = worker_db
    tenant_id = 999_000_003
    acquire_tenant_lock(conn_a, tenant_id)
    waits = []
    try:
        assert try_acquire_tenant_lock(second_conn, tenant_id, timeout_s=0.3, poll_s=0.05, on_wait=lambda: waits.append(1)) is None
        assert waits  # on_wait ran between polls, so a caller can keep its heartbeat fresh
    finally:
        release_tenant_lock(conn_a, tenant_id)
    assert try_acquire_tenant_lock(second_conn, tenant_id, timeout_s=0.3) is not None
    release_tenant_lock(second_conn, tenant_id)


def test_try_acquire_tenant_lock_never_sleeps_past_its_timeout(worker_db, second_conn):
    import time

    conn_a, _ = worker_db
    tenant_id = 999_000_004
    acquire_tenant_lock(conn_a, tenant_id)
    try:
        started = time.monotonic()
        # A poll interval far longer than the timeout must not stretch the call to a whole interval.
        assert try_acquire_tenant_lock(second_conn, tenant_id, timeout_s=0.2, poll_s=5.0) is None
        assert time.monotonic() - started < 1.0
    finally:
        release_tenant_lock(conn_a, tenant_id)
