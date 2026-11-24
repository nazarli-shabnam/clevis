"""Daily Actions-cache size points (``cache_size_points``, migration 0094) behind the Overview's growth chart.

Callers set the tenant session context first (RLS scopes every row by ``tenant_id``)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.db import CacheSizePoint


def record(db: Session, tenant_id: int, total_bytes: int, day: date | None = None) -> None:
    """Store today's total (the latest read of the day wins). Commits."""
    day = day or datetime.now(timezone.utc).date()
    stmt = pg_insert(CacheSizePoint).values(tenant_id=tenant_id, day=day, total_bytes=total_bytes)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "day"], set_={"total_bytes": total_bytes, "recorded_at": stmt.excluded.recorded_at}
        )
    )
    db.commit()


def list_points(db: Session, tenant_id: int, days: int) -> list[CacheSizePoint]:
    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    return list(
        db.execute(
            select(CacheSizePoint)
            .where(CacheSizePoint.tenant_id == tenant_id, CacheSizePoint.day >= since)
            .order_by(CacheSizePoint.day)
        ).scalars()
    )
