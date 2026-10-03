"""Read/write helpers for ``notification_destinations``. Callers must set the tenant session
context first (``require_org_role`` does); RLS scopes every row by ``tenant_id``."""

from __future__ import annotations

from sqlalchemy import func, select
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
