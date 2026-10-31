import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.db import Invitation, set_session_tenant
from src.repositories import tenant_repo

INVITATION_LIFETIME = timedelta(days=7)


class DuplicatePendingInvitation(Exception):
    """Raised by create() when an active pending invitation already exists for this (org, email).

    The partial unique index uq_invitations_org_email_pending makes the losing side of a
    concurrent double-insert fail."""

    def __init__(self, email: str):
        self.email = email
        super().__init__(f"A pending invitation already exists for {email} in this organization")


def _expire_lapsed(db: Session, org_id: int, email: str, exclude_id: int | None = None) -> None:
    """Expire lapsed 'pending' rows for this (org, email) so a re-invite isn't blocked.

    The partial unique index keys on status='pending', not on expiry. ``exclude_id`` spares the row
    being reissued: the bulk UPDATE doesn't sync the ORM object, so expiring it here would leave
    the session believing it is still 'pending' and the flush would skip writing its status."""
    query = db.query(Invitation).filter(
        Invitation.org_id == org_id,
        func.lower(Invitation.email) == email.lower(),
        Invitation.status == "pending",
        Invitation.expires_at <= datetime.now(timezone.utc),
    )
    if exclude_id is not None:
        query = query.filter(Invitation.id != exclude_id)
    query.update({Invitation.status: "expired"}, synchronize_session=False)


def create(
    db: Session, org_id: int, email: str, invited_by_user_id: int, *, commit: bool = True
) -> Invitation:
    """commit=False flushes into the caller's transaction, so the invitation and its audit row
    commit (or roll back) together."""
    _expire_lapsed(db, org_id, email)
    tenant = tenant_repo.get_or_create_org_tenant(db, org_id, commit=commit)
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
        db.flush()
        if commit:
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


def reissue(db: Session, invitation: Invitation) -> Invitation:
    """Give an existing invitation a fresh token and a fresh lifetime, making it pending again.

    The old link stops working. An expired invitation coming back to life can collide with a live
    pending invite for the same address (partial unique index), which surfaces as
    DuplicatePendingInvitation; a lapsed one is expired first, as create() does. Flushes without
    committing: the caller commits the rotation together with its audit row."""
    _expire_lapsed(db, invitation.org_id, invitation.email, exclude_id=invitation.id)
    invitation.token = secrets.token_urlsafe(32)
    invitation.expires_at = datetime.now(timezone.utc) + INVITATION_LIFETIME
    invitation.status = "pending"
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicatePendingInvitation(invitation.email) from exc
    db.refresh(invitation)
    return invitation


def get_by_token(db: Session, token: str) -> Invitation | None:
    # Token holders (preview/accept) have no tenant context yet; under the non-owner clevis_api
    # role RLS would hide the row. Resolve its tenant via the narrow SECURITY DEFINER lookup
    # (migration 0047) and set it first. The token itself is the capability.
    tenant_id = db.execute(text("SELECT invitation_tenant_by_token(:t)"), {"t": token}).scalar()
    if tenant_id is None:
        return None
    set_session_tenant(db, tenant_id)
    return db.query(Invitation).filter(Invitation.token == token).first()


def get_by_id_and_org(
    db: Session, invitation_id: int, org_id: int, *, for_update: bool = False
) -> Invitation | None:
    query = db.query(Invitation).filter(Invitation.id == invitation_id, Invitation.org_id == org_id)
    if for_update:
        query = query.with_for_update()
    return query.first()


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
