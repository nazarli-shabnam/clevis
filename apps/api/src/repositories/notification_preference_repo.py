from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.core.db import NotificationPreference


def get_muted(db: Session, user_id: int, tenant_id: int) -> set[str]:
    row = db.get(NotificationPreference, (user_id, tenant_id))
    return set(row.muted_kinds) if row else set()


def set_muted(db: Session, user_id: int, tenant_id: int, kinds: list[str]) -> list[str]:
    """Replace the user's muted kinds for the org (deduplicated, stable order); returns what was stored."""
    stored = sorted(set(kinds))
    stmt = insert(NotificationPreference).values(user_id=user_id, tenant_id=tenant_id, muted_kinds=stored)
    stmt = stmt.on_conflict_do_update(
        index_elements=[NotificationPreference.user_id, NotificationPreference.tenant_id],
        set_={"muted_kinds": stmt.excluded.muted_kinds},
    )
    db.execute(stmt)
    db.commit()
    return stored
