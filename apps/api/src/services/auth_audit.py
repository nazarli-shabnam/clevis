"""Audit rows for sign-in and session security events, so an admin can see after a suspected compromise when
someone signed in, failed to, or ended sessions.

The rows are attributed to the account's personal tenant (as ``config.update`` is): they concern the person, not
one organization, and ``GET /audit`` (workspace admins) reads every tenant. Filter by the ``auth.`` action prefix.
No credentials are ever written; the payload holds only the client IP, its user agent and the sign-in method.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import AuditLog, User, set_session_user
from src.repositories import audit_repo, tenant_repo

logger = logging.getLogger(__name__)

# One failed-login row per account per window: enough to show an attack happened and when it was seen, without
# letting a password-guessing loop turn into an audit-table flood.
FAILED_LOGIN_WINDOW = timedelta(minutes=15)
_MAX_AGENT_LEN = 200


def _payload(request: Request | None, extra: dict | None) -> dict:
    payload: dict = dict(extra or {})
    if request is not None:
        payload["ip"] = request.client.host if request.client else None
        payload["user_agent"] = (request.headers.get("user-agent") or "")[:_MAX_AGENT_LEN]
    return payload


def record(
    db: Session,
    user: User,
    action: str,
    *,
    request: Request | None = None,
    extra: dict | None = None,
    throttle: timedelta | None = None,
    commit: bool = True,
) -> None:
    """Write one ``auth.*`` row for ``user``. With ``commit=True`` (a sign-in) it never raises: losing an audit
    row must not block signing in, so a failure is logged and rolled back. With ``commit=False`` the row joins
    the caller's transaction (a revoke and its row land together) and a failure propagates, so the revoke fails
    visibly instead of being half-recorded. ``throttle`` skips the write when the same action was already
    recorded for this account within it."""
    try:
        user_id, email = user.id, user.email
        set_session_user(db, user_id)
        tenant_id = tenant_repo.ensure_personal_tenant(db, user_id, commit=False).id
        # audit_logs RLS is strict equality on app.tenant_id; audit_repo.write sets it for the insert, the read
        # below needs it too.
        db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
        if throttle is not None:
            since = datetime.now(timezone.utc) - throttle
            if (
                db.query(AuditLog.id)
                .filter(AuditLog.tenant_id == tenant_id, AuditLog.actor == email, AuditLog.action == action, AuditLog.created_at >= since)
                .first()
                is not None
            ):
                if commit:
                    db.rollback()
                return
        audit_repo.write(db, email, action, email, _payload(request, extra), tenant_id=tenant_id, commit=commit)
    except Exception:
        if not commit:
            raise
        logger.exception("could not write the %s audit row", action)
        db.rollback()
