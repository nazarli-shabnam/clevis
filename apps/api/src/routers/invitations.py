"""Org invitation router.

  POST   /orgs/{org_login}/invitations             admin: create a pending invite, returns a
                                                     shareable link (no email is sent)
  GET    /orgs/{org_login}/invitations             admin: list pending/accepted/revoked invites
  POST   /orgs/{org_login}/invitations/{id}/revoke admin: revoke a pending invite
  GET    /invitations/{token}                      unauthenticated: preview an invite by token
  POST   /invitations/{token}/accept               any authenticated user whose account email
                                                     case-insensitively matches the invite
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import Org, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import invitation_repo, org_membership_repo
from src.schemas.invitation import (
    InvitationAcceptResponse,
    InvitationCreate,
    InvitationCreateResponse,
    InvitationOut,
    InvitationPreview,
)

router = APIRouter()


def _ui_base() -> str:
    return settings.cors_origins[0] if settings.cors_origins else ""


@router.post("/orgs/{org_login}/invitations", response_model=InvitationCreateResponse)
def create_invitation(
    body: InvitationCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    invitation = invitation_repo.create(db, org_id=ctx.org.id, email=body.email, invited_by_user_id=user.id)
    return {
        "invitation": invitation,
        "invite_link": f"{_ui_base()}/invite/{invitation.token}",
    }


@router.get("/orgs/{org_login}/invitations", response_model=list[InvitationOut])
def list_invitations(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    return invitation_repo.list_for_org(db, org_id=ctx.org.id)


@router.post("/orgs/{org_login}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
def revoke_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
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
    return invitation


@router.get("/invitations/{token}", response_model=InvitationPreview)
def preview_invitation(token: str, db: Session = Depends(get_db)):
    invitation = invitation_repo.get_by_token(db, token)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    org = db.query(Org).filter(Org.id == invitation.org_id).first()
    return {"org_login": org.github_login, "email": invitation.email, "status": invitation.status}


@router.post("/invitations/{token}/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    token: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    invitation = invitation_repo.get_by_token(db, token)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invitation.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation is no longer pending")
    if invitation.email.lower() != user.email.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invitation email does not match account")

    membership = org_membership_repo.get_or_create(db, org_id=invitation.org_id, user_id=user.id, role="member")
    invitation.status = "accepted"
    invitation.accepted_at = datetime.now(timezone.utc)
    db.commit()

    org = db.query(Org).filter(Org.id == invitation.org_id).first()
    return {"org_login": org.github_login, "role": membership.role}
