import logging
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import Base

logger = logging.getLogger(__name__)

# Disposable local/CI Postgres hosts. Fail-closed allowlist for the truncate below, so an
# unrecognized (possibly real) host is refused by default.
_DISPOSABLE_DB_HOSTS = {"localhost", "127.0.0.1", "db"}


@pytest.fixture(scope="session")
def _engine():
    eng = create_engine(settings.database_url.get_secret_value())
    Base.metadata.create_all(eng)  # no-op if alembic already ran
    yield eng
    # Manual uvicorn/E2E sessions against this DB commit real rows (e.g. a workspace admin that
    # makes /auth/setup tests 409), so truncate once at session end to start the next run clean.
    host = urlsplit(settings.database_url.get_secret_value()).hostname
    if host not in _DISPOSABLE_DB_HOSTS:
        logger.warning("skipping post-session DB truncate: %r is not a recognized disposable-DB host", host)
        return

    # Best-effort: CI's constrained RLS roles deliberately lack TRUNCATE, so only the expected
    # privilege/lock errors are swallowed.
    try:
        with eng.begin() as conn:
            # TRUNCATE needs ACCESS EXCLUSIVE; bound the wait so a stray lock can't hang teardown.
            conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            # Unordered is fine: CASCADE handles FK order, and the orgs/tenants FK cycle makes
            # .sorted_tables warn.
            table_names = [table.name for table in Base.metadata.tables.values()]
            if table_names:
                quoted = ", ".join(f'"{name}"' for name in table_names)
                conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    except DBAPIError as exc:
        orig_type = type(exc.orig).__name__
        if orig_type not in ("InsufficientPrivilege", "LockNotAvailable"):
            raise
        logger.warning("skipping post-session DB truncate: %s", orig_type)


@pytest.fixture
def db(_engine):
    with _engine.connect() as conn:
        conn.begin()
        with Session(conn, join_transaction_mode="create_savepoint") as session:
            yield session
        conn.rollback()
        # rbac.py sets app.tenant_id/app.user_id via plain SET, which rollback() doesn't clear;
        # reset like get_db() does so they can't leak to the next test on a pooled connection.
        conn.execute(text("RESET app.tenant_id"))
        conn.execute(text("RESET app.user_id"))
        # RESET is transactional; commit or closing the connection rolls it back.
        conn.commit()
