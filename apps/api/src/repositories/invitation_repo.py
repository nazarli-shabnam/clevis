import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.db import Invitation
from src.repositories import tenant_repo

INVITATION_LIFETIME = timedelta(days=7)


class DuplicatePendingInvitation(Exception):
    """Raised by create() when an active pending invitation already exists for this (org, email).

    The partial unique index uq_invitations_org_email_pending makes the losing side of a
    concurrent double-insert fail."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"A pending invitation already exists for {email} in this organization")


def _expire_lapsed(db: Session, org_id: int, email: str) -> None:
    """Expire lapsed 'pending' rows for this (org, email) so a re-invite isn't blocked.

    The partial unique index keys on status='pending', not on expiry."""
    db.query(Invitation).filter(
        Invitation.org_id == org_id,
        func.lower(Invitation.email) == email.lower(),
        Invitation.status == "pending",
        Invitation.expires_at <= datetime.now(timezone.utc),
    ).update({Invitation.status: "expired"}, synchronize_session=False)


def create(db: Session, org_id: int, email: str, invited_by_user_id: int) -> Invitation:
    _expire_lapsed(db, org_id, email)
    tenant = tenant_repo.get_or_create_org_tenant(db, org_id)
    invitation = Invitation(
        org_id=org_id,
        email=email,
        token=secrets.token_urlsafe(32),
        status="pending",
        invited_by_user_id=invited_by_user_id,
        expires_at=datetime.now(timezone.utc) + INVITATION_LIFETIME,
        tenant_id=tenant.id,
    )
    db.add(invitation)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Only a duplicate pending invite (the concurrent-insert race) becomes a 409; any other
        # IntegrityError must surface as itself.
        if get_pending_for_org_and_email(db, org_id=org_id, email=email) is None:
            raise
        raise DuplicatePendingInvitation(email) from exc
    db.refresh(invitation)
    return invitation


def get_by_token(db: Session, token: str) -> Invitation | None:
    return db.query(Invitation).filter(Invitation.token == token).first()


def get_by_id_and_org(db: Session, invitation_id: int, org_id: int) -> Invitation | None:
    return (
        db.query(Invitation)
        .filter(Invitation.id == invitation_id, Invitation.org_id == org_id)
        .first()
    )


def list_for_org(db: Session, org_id: int) -> list[Invitation]:
    return db.query(Invitation).filter(Invitation.org_id == org_id).order_by(Invitation.created_at.desc()).all()


def get_pending_for_org_and_email(db: Session, org_id: int, email: str) -> Invitation | None:
    return (
        db.query(Invitation)
        .filter(
            Invitation.org_id == org_id,
            # Case-insensitive *exact* match: ilike() would treat `_`/`%` in the address as wildcards.
            func.lower(Invitation.email) == email.lower(),
            Invitation.status == "pending",
            Invitation.expires_at > datetime.now(timezone.utc),
        )
        .first()
    )


def list_pending_for_email(db: Session, email: str) -> list[Invitation]:
    return (
        db.query(Invitation)
        .filter(
            # Exact, case-insensitive -- see get_pending_for_org_and_email.
            func.lower(Invitation.email) == email.lower(),
            Invitation.status == "pending",
            Invitation.expires_at > datetime.now(timezone.utc),
        )
        .all()
    )
