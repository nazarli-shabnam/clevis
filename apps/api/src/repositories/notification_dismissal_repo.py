from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.core.db import NotificationDismissal

# Longer than the widest feed window the API serves, so a dismissal never expires while its item can still be listed.
_KEEP_DAYS = 120


def get_dismissed(db: Session, user_id: int, tenant_id: int) -> dict[str, datetime]:
    """item_id -> when this user dismissed it, so an item whose own date moves later (a repeat permission drift
    keeps the installation's id) is not still treated as dismissed."""
    rows = db.execute(
        select(NotificationDismissal.item_id, NotificationDismissal.dismissed_at).where(
            NotificationDismissal.user_id == user_id, NotificationDismissal.tenant_id == tenant_id
        )
    )
    return {item_id: dismissed_at for item_id, dismissed_at in rows}


def dismiss(db: Session, user_id: int, tenant_id: int, item_id: str) -> None:
    """Mark one item read for this user. Idempotent; also prunes this user's marks for items long out of any window."""
    db.execute(
        insert(NotificationDismissal)
        .values(user_id=user_id, tenant_id=tenant_id, item_id=item_id)
        .on_conflict_do_nothing(index_elements=["user_id", "tenant_id", "item_id"])
    )
    db.execute(
        delete(NotificationDismissal).where(
            NotificationDismissal.user_id == user_id,
            NotificationDismissal.tenant_id == tenant_id,
            NotificationDismissal.dismissed_at < datetime.now(timezone.utc) - timedelta(days=_KEEP_DAYS),
        )
    )
    db.commit()
