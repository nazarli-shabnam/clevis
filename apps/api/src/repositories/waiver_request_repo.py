"""Read/write helpers for ``waiver_requests``. Callers must set the tenant session context first
(``require_org_role`` does); RLS scopes every row by ``tenant_id``."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.core.db import WaiverRequest

_LIST_LIMIT = 200


def list_for_tenant(
    db: Session, tenant_id: int, *, status: str | None = None, requested_by_user_id: int | None = None
) -> list[WaiverRequest]:
    """Newest first. ``requested_by_user_id`` narrows to one member's own requests."""
    stmt = select(WaiverRequest).where(WaiverRequest.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(WaiverRequest.status == status)
    if requested_by_user_id is not None:
        stmt = stmt.where(WaiverRequest.requested_by_user_id == requested_by_user_id)
    return list(db.execute(stmt.order_by(WaiverRequest.created_at.desc(), WaiverRequest.id.desc()).limit(_LIST_LIMIT)).scalars())


def get(db: Session, tenant_id: int, request_id: int, *, lock: bool = False) -> WaiverRequest | None:
    stmt = select(WaiverRequest).where(WaiverRequest.tenant_id == tenant_id, WaiverRequest.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def count_pending(db: Session, tenant_id: int, *, requested_by_user_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(WaiverRequest).where(
        WaiverRequest.tenant_id == tenant_id, WaiverRequest.status == "pending"
    )
    if requested_by_user_id is not None:
        stmt = stmt.where(WaiverRequest.requested_by_user_id == requested_by_user_id)
    return db.execute(stmt).scalar_one()


def create(db: Session, **fields) -> WaiverRequest:
    request = WaiverRequest(**fields)
    db.add(request)
    db.flush()
    return request
