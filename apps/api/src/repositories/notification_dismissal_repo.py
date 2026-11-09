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
    """Mark one item read for this user, as of now. A repeat dismissal refreshes the time, so an item that changed
    since (a new permission drift keeps its id) can be dismissed again. Also prunes this user's marks for items long
    out of any window."""
    # The app's clock, not the database's: it is compared with item times the app also stamped (e.g.
    # ``permissions_changed_at``), so a skew between the two machines cannot reorder them.
    now = datetime.now(timezone.utc)
    db.execute(
        insert(NotificationDismissal)
        .values(user_id=user_id, tenant_id=tenant_id, item_id=item_id, dismissed_at=now)
        .on_conflict_do_update(
            index_elements=["user_id", "tenant_id", "item_id"], set_={"dismissed_at": now}
        )
    )
    db.execute(
        delete(NotificationDismissal).where(
            NotificationDismissal.user_id == user_id,
            NotificationDismissal.tenant_id == tenant_id,
            NotificationDismissal.dismissed_at < now - timedelta(days=_KEEP_DAYS),
        )
    )
    db.commit()
