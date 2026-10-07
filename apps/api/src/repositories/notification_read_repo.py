from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.core.db import NotificationRead


def get_last_read(db: Session, user_id: int, tenant_id: int) -> datetime | None:
    row = db.get(NotificationRead, (user_id, tenant_id))
    return row.last_read_at if row else None


def mark_read(db: Session, user_id: int, tenant_id: int, at: datetime) -> None:
    """Upsert the marker. It only moves forward, so a delayed request from another tab can't un-read items."""
    stmt = insert(NotificationRead).values(user_id=user_id, tenant_id=tenant_id, last_read_at=at)
    stmt = stmt.on_conflict_do_update(
        index_elements=[NotificationRead.user_id, NotificationRead.tenant_id],
        set_={"last_read_at": stmt.excluded.last_read_at},
        where=NotificationRead.last_read_at < stmt.excluded.last_read_at,
    )
    db.execute(stmt)
    db.commit()
