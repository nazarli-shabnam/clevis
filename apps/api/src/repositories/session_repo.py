"""Issued-session records behind Profile's "active sessions" list (see migration 0077).

Revocation is still the ``revoked_tokens`` denylist that ``require_auth`` checks; this table only exists
so a user can see their sessions and pick one to end. Callers own the commit.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.core.db import RevokedToken, UserSession

# last_seen_at is written at most this often per session, so authenticating does not write on every request.
TOUCH_INTERVAL = timedelta(minutes=1)
_USER_AGENT_MAX = 255
_IP_MAX = 45  # longest textual IPv6 address


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    db: Session, *, jti: str, user_id: int, expires_at: datetime, user_agent: str | None, ip: str | None
) -> None:
    """Store a new session and drop this user's sessions that have already expired."""
    db.execute(delete(UserSession).where(UserSession.user_id == user_id, UserSession.expires_at < _now()))
    db.add(
        UserSession(
            jti=jti,
            user_id=user_id,
            expires_at=expires_at,
            user_agent=user_agent[:_USER_AGENT_MAX] if user_agent else None,
            ip=ip[:_IP_MAX] if ip else None,
        )
    )


def touch(db: Session, jti: str) -> bool:
    """Move last_seen_at to now unless it was moved within TOUCH_INTERVAL. True when a row was written."""
    now = _now()
    result = db.execute(
        update(UserSession)
        .where(UserSession.jti == jti, UserSession.last_seen_at < now - TOUCH_INTERVAL)
        .values(last_seen_at=now)
    )
    return result.rowcount > 0


def list_active(db: Session, user_id: int) -> list[UserSession]:
    return list(
        db.execute(
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.expires_at > _now())
            .order_by(UserSession.last_seen_at.desc(), UserSession.jti)
        ).scalars()
    )


def revoke(db: Session, user_id: int, jti: str) -> bool:
    """End one of the user's sessions: denylist its jti until it would have expired, then forget it.

    False when the user has no such session (another user's id is indistinguishable from an unknown one)."""
    row = db.execute(
        select(UserSession).where(UserSession.jti == jti, UserSession.user_id == user_id)
    ).scalar_one_or_none()
    if row is None:
        return False
    db.execute(
        insert(RevokedToken)
        .values(jti=row.jti, user_id=user_id, expires_at=row.expires_at)
        .on_conflict_do_nothing(index_elements=["jti"])
    )
    db.delete(row)
    return True


def forget(db: Session, jtis: list[str]) -> None:
    """Drop the records for sessions that were just ended some other way (logout)."""
    if jtis:
        db.execute(delete(UserSession).where(UserSession.jti.in_(jtis)))


def forget_all(db: Session, user_id: int) -> None:
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
