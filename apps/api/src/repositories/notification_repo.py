"""Read/write helpers for ``notification_destinations``. Callers must set the tenant session
context first (``require_org_role`` does); RLS scopes every row by ``tenant_id``."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.core.db import NotificationDestination


def list_for_tenant(db: Session, tenant_id: int) -> list[NotificationDestination]:
    return list(
        db.execute(
            select(NotificationDestination)
            .where(NotificationDestination.tenant_id == tenant_id)
            .order_by(NotificationDestination.id)
        ).scalars()
    )


def count_for_tenant(db: Session, tenant_id: int) -> int:
    return db.execute(
        select(func.count()).select_from(NotificationDestination).where(NotificationDestination.tenant_id == tenant_id)
    ).scalar_one()


def get(db: Session, tenant_id: int, destination_id: int) -> NotificationDestination | None:
    return db.execute(
        select(NotificationDestination).where(
            NotificationDestination.tenant_id == tenant_id, NotificationDestination.id == destination_id
        )
    ).scalar_one_or_none()


def create(db: Session, **fields) -> NotificationDestination:
    dest = NotificationDestination(**fields)
    db.add(dest)
    db.flush()
    return dest


def delete(db: Session, dest: NotificationDestination) -> None:
    db.delete(dest)
    db.flush()


def claim_for_alert(db: Session, destination_id: int, cooldown: timedelta) -> bool:
    """Atomically mark a destination as alerted now; False if it was already alerted within ``cooldown``.

    One conditional UPDATE, so of two concurrent scans only one claims the row (the other re-evaluates
    the WHERE after the first commits). Committed here so the claim is visible before we message."""
    claimed = db.execute(
        update(NotificationDestination)
        .where(
            NotificationDestination.id == destination_id,
            (NotificationDestination.last_notified_at.is_(None))
            | (NotificationDestination.last_notified_at < func.now() - cooldown),
        )
        .values(last_notified_at=func.now())
    ).rowcount
    db.commit()
    return claimed == 1


def release_alert_claim(db: Session, destination_id: int) -> None:
    """Undo a claim after a failed delivery so the next drop can retry instead of waiting out the cooldown."""
    db.execute(
        update(NotificationDestination).where(NotificationDestination.id == destination_id).values(last_notified_at=None)
    )
    db.commit()
