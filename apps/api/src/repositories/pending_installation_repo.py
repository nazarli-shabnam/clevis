"""``pending_installations``: installations GitHub announced that no Clevis org has connected yet.

Not tenant-scoped (see ``core.db.PendingInstallation``). Callers never expose a row to someone who is not its
sender or requester."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.core.db import PendingInstallation

# A notice nobody acted on is stale: the installation was probably connected another way or abandoned.
MAX_AGE = timedelta(days=30)


def record(
    db: Session,
    *,
    installation_id: int,
    account_login: str,
    account_id: int | None,
    sender_github_id: int | None,
    sender_login: str | None,
    requester_github_id: int | None,
    requester_login: str | None,
) -> None:
    """Remember (or refresh) an announced installation, and drop notices older than ``MAX_AGE``. Commits."""
    values = dict(
        installation_id=installation_id,
        account_login=account_login,
        account_id=account_id,
        sender_github_id=sender_github_id,
        sender_login=sender_login,
        requester_github_id=requester_github_id,
        requester_login=requester_login,
    )
    stmt = insert(PendingInstallation).values(**values)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[PendingInstallation.installation_id],
            set_={k: v for k, v in values.items() if k != "installation_id"},
        )
    )
    db.execute(delete(PendingInstallation).where(PendingInstallation.created_at < datetime.now(timezone.utc) - MAX_AGE))
    db.commit()


def list_for_github_user(db: Session, github_user_id: int) -> list[PendingInstallation]:
    """Notices where this GitHub user approved (sender) or asked for (requester) the installation, newest first."""
    return list(
        db.execute(
            select(PendingInstallation)
            .where(
                or_(
                    PendingInstallation.sender_github_id == github_user_id,
                    PendingInstallation.requester_github_id == github_user_id,
                ),
                # The purge in record() only runs when a new event arrives, so age is also enforced on read.
                PendingInstallation.created_at >= datetime.now(timezone.utc) - MAX_AGE,
            )
            .order_by(PendingInstallation.created_at.desc(), PendingInstallation.installation_id.desc())
        ).scalars()
    )


def delete_by_installation_id(db: Session, installation_id: int) -> None:
    """Forget a notice (connected, or the installation was deleted). Commits."""
    db.execute(delete(PendingInstallation).where(PendingInstallation.installation_id == installation_id))
    db.commit()
