"""Shared shape for the API's periodic background sweep loops (gap-heal, membership
reconcile, leadership digest). The API is already a long-running uvicorn process (unlike
a request-scoped handler), so an asyncio while-True-plus-sleep loop is the smallest thing
that satisfies "runs periodically" without pulling in APScheduler/Celery/cron or a new
container -- mirrors apps/worker/src/worker.py's own poll loop, in-process instead.

Each loop's per-tick session bypasses get_db()'s own finally-block reset (a request-scoped
session's lifetime doesn't fit here), so _run_sweep resets app.tenant_id/app.user_id itself
after the sweep runs -- the sweep sets them via plain SET (not SET LOCAL) and commits, which
makes the SET durable on the physical connection, not just the Session. Without this reset,
the connection pool would hand the next checkout (a real request via get_db(), or the next
sweep iteration) a connection with a leftover tenant context.
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.db import SessionLocal

log = logging.getLogger(__name__)


def _read_poll_seconds(*, config_key: str, default_seconds: int, min_seconds: int, max_seconds: int) -> int:
    raw = get_config(config_key, str(default_seconds))
    try:
        return max(min_seconds, min(max_seconds, int(raw)))
    except ValueError:
        log.warning("%s %r is not an integer; using %d", config_key, raw, default_seconds)
        return default_seconds


def _run_sweep(sweep_fn: Callable[[Session], None]) -> None:
    with SessionLocal() as db:
        try:
            sweep_fn(db)
        finally:
            db.rollback()
            db.execute(text("RESET app.tenant_id"))
            db.execute(text("RESET app.user_id"))
            db.commit()


async def run_sweep_loop(
    *,
    label: str,
    sweep_fn: Callable[[Session], None],
    config_key: str,
    default_seconds: int,
    min_seconds: int,
    max_seconds: int,
) -> None:
    """Run sweep_fn in a thread, then sleep, forever. A single iteration's exception never
    kills the loop -- it's logged and the loop keeps ticking on the next cycle."""
    log.info("%s sweep loop started", label)
    while True:
        try:
            await asyncio.to_thread(_run_sweep, sweep_fn)
        except Exception:
            log.exception("%s sweep iteration failed", label)
        await asyncio.sleep(
            _read_poll_seconds(
                config_key=config_key, default_seconds=default_seconds, min_seconds=min_seconds, max_seconds=max_seconds
            )
        )
