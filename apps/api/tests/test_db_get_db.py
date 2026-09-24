"""get_db() must reset app.tenant_id/app.user_id on checkin: plain SET persists on the pooled
connection and would leak into the next request. Uses the real pool, not the `db` fixture."""

from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import get_db


def _session_context_via_new_connection() -> tuple[str | None, str | None]:
    gen = get_db()
    db = next(gen)
    try:
        tenant, user = db.execute(
            text("SELECT current_setting('app.tenant_id', true), current_setting('app.user_id', true)")
        ).one()
    finally:
        gen.close()
    return (tenant or None, user or None)


def test_get_db_resets_tenant_context_before_connection_checkin():
    gen = get_db()
    db = next(gen)
    db.execute(text("SET app.tenant_id = 555555"))
    db.execute(text("SET app.user_id = 777777"))
    db.commit()
    assert db.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() == "555555"
    gen.close()  # triggers get_db()'s finally: block

    # Reuse of the same physical connection isn't guaranteed, so poll a few; a missed reset
    # shows up on at least one of them.
    seen = {_session_context_via_new_connection() for _ in range(5)}
    assert seen == {(None, None)}, f"session context leaked into a reused connection: {seen}"


def test_get_db_invalidates_the_connection_if_the_reset_itself_fails():
    # If RESET/commit fails, get_db() must invalidate the connection, not return it dirty.
    original_commit = Session.commit
    calls = {"n": 0}

    def failing_commit(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated failure during tenant-context reset")
        return original_commit(self)

    gen = get_db()
    next(gen)
    with patch.object(Session, "commit", failing_commit), patch.object(Session, "invalidate") as mock_invalidate:
        gen.close()

    mock_invalidate.assert_called_once()
