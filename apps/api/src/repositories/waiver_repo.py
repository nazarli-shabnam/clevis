"""Read/write helpers for ``check_waivers``. Callers must set the tenant session context first
(``require_org_role`` does); RLS scopes every row by ``tenant_id``."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import CheckWaiver


def list_for_tenant(db: Session, tenant_id: int) -> list[CheckWaiver]:
    return list(
        db.execute(
            select(CheckWaiver).where(CheckWaiver.tenant_id == tenant_id).order_by(CheckWaiver.expires_at, CheckWaiver.id)
        ).scalars()
    )


def active_for_tenant(db: Session, tenant_id: int, now: datetime) -> list[CheckWaiver]:
    return list(
        db.execute(
            select(CheckWaiver).where(CheckWaiver.tenant_id == tenant_id, CheckWaiver.expires_at > now)
        ).scalars()
    )


def due_for_expiry_notice(db: Session, tenant_id: int, now: datetime) -> list[CheckWaiver]:
    return list(
        db.execute(
            select(CheckWaiver)
            .where(CheckWaiver.tenant_id == tenant_id, CheckWaiver.expires_at <= now, CheckWaiver.expiry_noted_at.is_(None))
            .order_by(CheckWaiver.expires_at)
        ).scalars()
    )


def get(db: Session, tenant_id: int, waiver_id: int) -> CheckWaiver | None:
    return db.execute(
        select(CheckWaiver).where(CheckWaiver.tenant_id == tenant_id, CheckWaiver.id == waiver_id)
    ).scalar_one_or_none()


def count_for_tenant(db: Session, tenant_id: int) -> int:
    return len(list_for_tenant(db, tenant_id))


def create(db: Session, **fields) -> CheckWaiver:
    waiver = CheckWaiver(**fields)
    db.add(waiver)
    db.flush()
    return waiver


def delete(db: Session, waiver: CheckWaiver) -> None:
    db.delete(waiver)
    db.flush()
