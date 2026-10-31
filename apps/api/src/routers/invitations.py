"""Org invitation router: admin create (single or bulk) / list / revoke / regenerate pending invites
(shareable link; an email goes out only on an explicit resend), unauthenticated preview by token,
and accept -- which requires the caller's account email to case-insensitively match the invite
AND be verified.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import Org, User, get_db
from src.core.rate_limit import rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, invitation_repo, org_membership_repo
from src.schemas.invitation import (
    InvitationAcceptResponse,
    InvitationBulkCreate,
    InvitationBulkItem,
    InvitationBulkResponse,
    InvitationCreate,
    InvitationCreateResponse,
    InvitationOut,
    InvitationPreview,
)
from src.services import email

router = APIRouter()
logger = logging.getLogger(__name__)
_EMAIL = TypeAdapter(EmailStr)


def _ui_base() -> str:
    return settings.cors_origins[0] if settings.cors_origins else ""


def _invite_link(invitation) -> str:
    return f"{_ui_base()}/invite/{invitation.token}"


def _is_expired(invitation) -> bool:
    return invitation.status == "pending" and invitation.expires_at <= datetime.now(timezone.utc)


def _effective_status(invitation) -> str:
    return "expired" if _is_expired(invitation) else invitation.status


def _expire(db: Session, invitation) -> None:
    invitation.status = "expired"
    db.commit()
    db.refresh(invitation)


@router.post("/orgs/{org_login}/invitations", response_model=InvitationCreateResponse)
def create_invitation(
    body: InvitationCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    existing = invitation_repo.get_pending_for_org_and_email(db, org_id=ctx.org.id, email=body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pending invitation already exists for {body.email} in this organization",
        )
    try:
        invitation = invitation_repo.create(db, org_id=ctx.org.id, email=body.email, invited_by_user_id=user.id)
    except invitation_repo.DuplicatePendingInvitation as exc:
        # A concurrent request won the race between the pre-check above and its own
        # insert; the DB constraint rejected this one.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_repo.write(
        db, user.email, "invitation.create", ctx.org.github_login, {"email": body.email},
        tenant_id=ctx.org.tenant_id,
    )
    return {
        "invitation": invitation,
        "invite_link": _invite_link(invitation),
    }


@router.post(
    "/orgs/{org_login}/invitations/bulk",
    response_model=InvitationBulkResponse,
    dependencies=[Depends(rate_limit())],
)
def create_invitations_bulk(
    body: InvitationBulkCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Invite several addresses at once. Each distinct address gets its own result, so one bad or
    duplicate address never blocks the rest; an address repeated in the request (compared
    case-insensitively) is reported once, for its first occurrence."""
    results: list[InvitationBulkItem] = []
    seen: set[str] = set()
    for raw in body.emails:
        try:
            address = _EMAIL.validate_python(raw.strip())
        except ValidationError:
            results.append(InvitationBulkItem(email=raw.strip(), status="invalid"))
            continue
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        if invitation_repo.get_pending_for_org_and_email(db, org_id=ctx.org.id, email=address) is not None:
            results.append(InvitationBulkItem(email=address, status="already_pending"))
            continue
        # One transaction per address: the invitation and its audit row land together or not at all,
        # so a failure never leaves an invitation whose link and audit trail are both missing.
        try:
            invitation = invitation_repo.create(
                db, org_id=ctx.org.id, email=address, invited_by_user_id=user.id, commit=False
            )
            audit_repo.write(
                db, user.email, "invitation.create", ctx.org.github_login, {"email": address},
                tenant_id=ctx.org.tenant_id, commit=False,
            )
            db.commit()
        except invitation_repo.DuplicatePendingInvitation:
            results.append(InvitationBulkItem(email=address, status="already_pending"))
            continue
        except SQLAlchemyError:
            logger.warning("Bulk invitation for org %s failed", ctx.org.github_login, exc_info=True)
            db.rollback()
            results.append(InvitationBulkItem(email=address, status="failed"))
            continue
        db.refresh(invitation)
        results.append(
            InvitationBulkItem(
                email=address, status="created", invitation=invitation, invite_link=_invite_link(invitation)
            )
        )
    return {"results": results}


@router.get("/orgs/{org_login}/invitations", response_model=list[InvitationOut])
def list_invitations(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    invitations = invitation_repo.list_for_org(db, org_id=ctx.org.id)
    return [
        InvitationOut(
            id=inv.id,
            org_id=inv.org_id,
            email=inv.email,
            status=_effective_status(inv),
            created_at=inv.created_at,
            accepted_at=inv.accepted_at,
            expires_at=inv.expires_at,
        )
        for inv in invitations
    ]


@router.post("/orgs/{org_login}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
def revoke_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    invitation = invitation_repo.get_by_id_and_org(db, invitation_id=invitation_id, org_id=ctx.org.id)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invitation.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation is not pending")
    invitation.status = "revoked"
    db.commit()
    db.refresh(invitation)
    audit_repo.write(
        db, user.email, "invitation.revoke", ctx.org.github_login, {"email": invitation.email},
        tenant_id=ctx.org.tenant_id,
    )
    return invitation


def _reissue(db: Session, ctx: OrgContext, invitation_id: int):
    """Rotate the token of a pending or expired invitation of this org (accepted and revoked ones
    are final). Returns the invitation, now pending with a new link.

    The rotation is flushed, not committed: the caller's audit_repo.write (default commit=True)
    commits it together with its audit row. The row is locked from the status check on, so a
    concurrent create() can't expire it between the check and the write."""
    invitation = invitation_repo.get_by_id_and_org(
        db, invitation_id=invitation_id, org_id=ctx.org.id, for_update=True
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if _effective_status(invitation) not in ("pending", "expired"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only pending or expired invitations can be reissued"
        )
    try:
        return invitation_repo.reissue(db, invitation)
    except invitation_repo.DuplicatePendingInvitation as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/orgs/{org_login}/invitations/{invitation_id}/regenerate",
    response_model=InvitationCreateResponse,
    dependencies=[Depends(rate_limit())],
)
def regenerate_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """New link and a fresh 7 days for an existing invitation; the previous link stops working."""
    invitation = _reissue(db, ctx, invitation_id)
    audit_repo.write(
        db, user.email, "invitation.regenerate", ctx.org.github_login, {"email": invitation.email},
        tenant_id=ctx.org.tenant_id,
    )
    return {"invitation": invitation, "invite_link": _invite_link(invitation)}


@router.post(
    "/orgs/{org_login}/invitations/{invitation_id}/resend",
    response_model=InvitationOut,
    dependencies=[Depends(rate_limit())],
)
def resend_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Email a fresh link to the invitee. Needs SMTP; the previous link stops working."""
    # Checked first so an instance without SMTP doesn't rotate (and so lose) a link for nothing.
    if not email.is_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email isn't set up on this instance (SMTP_HOST and SMTP_FROM). Use Regenerate link and share it yourself.",
        )
    invitation = _reissue(db, ctx, invitation_id)
    audit_repo.write(
        db, user.email, "invitation.resend", ctx.org.github_login, {"email": invitation.email},
        tenant_id=ctx.org.tenant_id,
    )
    try:
        email.send_invitation_email(invitation.email, ctx.org.github_login, user.email, _invite_link(invitation))
    except OSError:  # smtplib.SMTPException and ssl.SSLError are OSErrors
        logger.warning("Failed to email invitation %s", invitation.id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The email couldn't be sent. The previous link no longer works; use Regenerate link to get a new one.",
        )
    return invitation


@router.get("/invitations/{token}", response_model=InvitationPreview)
def preview_invitation(token: str, db: Session = Depends(get_db)):
    invitation = invitation_repo.get_by_token(db, token)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if _is_expired(invitation):
        _expire(db, invitation)
    org = db.query(Org).filter(Org.id == invitation.org_id).first()
    return {"org_login": org.github_login, "status": invitation.status}


@router.post("/invitations/{token}/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    token: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    invitation = invitation_repo.get_by_token(db, token)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if _is_expired(invitation):
        _expire(db, invitation)
    if invitation.status == "expired":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has expired")
    if invitation.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation is no longer pending")
    if invitation.email.lower() != user.email.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation email does not match account")
    # Email match alone isn't proof of ownership for a self-registered account.
    # Accounts created by GitHub sign-in and the setup admin are verified immediately;
    # self-registered accounts must click the emailed verification link first.
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user is None or not db_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email before accepting this invitation",
        )

    # An existing membership (e.g. a GitHub-verified admin) is kept as-is: accepting a member
    # invite must never demote it.
    membership = org_membership_repo.create_if_missing(
        db, org_id=invitation.org_id, user_id=user.id, role="member", source="invite"
    )
    invitation.status = "accepted"
    invitation.accepted_at = datetime.now(timezone.utc)
    db.commit()

    org = db.query(Org).filter(Org.id == invitation.org_id).first()
    audit_repo.write(
        db, user.email, "invitation.accept", org.github_login, {"role": membership.role},
        tenant_id=org.tenant_id,
    )
    return {"org_login": org.github_login, "role": membership.role}
