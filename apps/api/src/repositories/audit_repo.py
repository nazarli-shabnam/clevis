import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import AuditLog


def write(
    db: Session, actor: str, action: str, target: str, payload: dict, tenant_id: int | None = None,
    *, commit: bool = True,
) -> None:
    """commit=False flushes into the caller's transaction, so a mutation and its audit row
    commit (or roll back) together."""
    # audit_logs RLS is strict equality on app.tenant_id, and many callers never set it. SET LOCAL
    # to exactly the value being written is safe (not caller-controlled) and scoped to this transaction.
    if tenant_id is not None:
        db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
    db.add(AuditLog(actor=actor, action=action, target=target, payload=json.dumps(payload), tenant_id=tenant_id))
    if commit:
        db.commit()
    else:
        db.flush()


def _like_escape(value: str) -> str:
    """Make `value` match literally inside a LIKE pattern (so a filter of "50%" isn't a wildcard)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_for_tenant(
    db: Session,
    tenant_id: int,
    *,
    action_prefix: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    """One tenant's audit rows, newest first. The tenant is filtered explicitly (not only by the
    audit_logs RLS policy, which is inert for a superuser connection); rows with no tenant, such as
    those written before attribution existed, never match."""
    q = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
    if action_prefix:
        q = q.filter(AuditLog.action.like(f"{_like_escape(action_prefix)}%", escape="\\"))
    if actor:
        q = q.filter(AuditLog.actor.ilike(_like_escape(actor), escape="\\"))
    if target:
        q = q.filter(AuditLog.target.ilike(f"%{_like_escape(target)}%", escape="\\"))
    if since is not None:
        q = q.filter(AuditLog.created_at >= since)
    if until is not None:
        q = q.filter(AuditLog.created_at < until)
    if before_id is not None:
        q = q.filter(AuditLog.id < before_id)
    return q.order_by(AuditLog.id.desc()).limit(limit).all()
