"""Read/write helpers for ``check_waivers``. Callers must set the tenant session context first
(``require_org_role`` does); RLS scopes every row by ``tenant_id``."""

from datetime import datetime, timedelta

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


def candidates_for_expiry_warning(
    db: Session, tenant_id: int, now: datetime, widest_window_days: int, *, lock: bool = False
) -> list[CheckWaiver]:
    """Unexpired waivers that lapse within the widest warning window and have not yet had every warning stage
    announced. The caller works out the stage each has reached (``services.waivers.warning_stage``). ``lock``
    takes row locks held to the end of the transaction, for a caller that is about to write a stage computed from
    these rows: a renewal then waits for it (or was already committed and is read here), so a stale stage can
    never be written over a renewed expiry."""
    stmt = (
        select(CheckWaiver)
        .where(
            CheckWaiver.tenant_id == tenant_id,
            CheckWaiver.expires_at > now,
            CheckWaiver.expires_at <= now + timedelta(days=widest_window_days),
        )
        .order_by(CheckWaiver.expires_at)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list(db.execute(stmt).scalars())


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
