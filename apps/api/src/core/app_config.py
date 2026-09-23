"""Runtime config backed by the app_config table, cached in memory for 60s.

The cache is a module-level dict to avoid circular imports; a TTL race costs at most one extra DB read.
"""

import logging
import time

from sqlalchemy import text

logger = logging.getLogger(__name__)

_ACCEPTED_KEYS = {
    "worker_poll_seconds",
    "registration_enabled",
    "gap_heal_poll_seconds",
    "gap_heal_stale_hours",
    "membership_reconcile_poll_seconds",
    "membership_reconcile_stale_hours",
    "pr_nudge_stale_days",
    "pr_nudge_mode",
    "digest_poll_seconds",
    "digest_cadence",
    "webhook_requeue_poll_seconds",
}
_TTL = 60.0
_cache: dict[str, tuple[str, float]] = {}


def get_config(key: str, default: str = "") -> str:
    """Return the value for *key* from app_config, falling back to *default*."""
    now = time.monotonic()
    if key in _cache:
        val, ts = _cache[key]
        if now - ts < _TTL:
            return val

    # Import here to avoid circular dependency (db imports config)
    from src.core.db import SessionLocal  # noqa: PLC0415

    try:
        with SessionLocal() as db:
            row = db.execute(
                text("SELECT value FROM app_config WHERE key = :key"), {"key": key}
            ).fetchone()
        val = row[0] if row else default
    except Exception:
        # On a DB error (blip or missing SELECT grant) serve the last-known-good value rather than
        # flip a security setting like registration_enabled to its default. Don't refresh its timestamp.
        if key in _cache:
            logger.error("app_config read failed for key %r, serving last-known-good cached value", key)
            return _cache[key][0]
        logger.error("app_config read failed for key %r and no cached value exists, using default", key)
        return default

    _cache[key] = (val, now)
    return val


def read_all() -> dict[str, str]:
    """Return all app_config rows as a plain dict (no caching)."""
    from src.core.db import SessionLocal  # noqa: PLC0415

    with SessionLocal() as db:
        rows = db.execute(text("SELECT key, value FROM app_config")).fetchall()
    return {r[0]: r[1] for r in rows}


def set_config(key: str, value: str) -> None:
    """Upsert *key* → *value* (re-creating a missing row) and invalidate its cache entry."""
    if key not in _ACCEPTED_KEYS:
        raise ValueError(f"Unknown config key: {key!r}")

    from src.core.db import SessionLocal  # noqa: PLC0415

    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO app_config (key, value, updated_at) VALUES (:key, :value, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
            ),
            {"key": key, "value": value},
        )
        db.commit()

    _cache.pop(key, None)
