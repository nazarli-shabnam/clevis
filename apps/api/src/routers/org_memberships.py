"""Who has Clevis access to an org, and what an org admin can do about it.

GitHub-sourced memberships mirror the person's GitHub org status and are rewritten by every GitHub
reconciliation, so a manual change would not last: they are listed but read-only. Invite-sourced ones
(an accepted email invitation, e.g. a contractor) are Clevis's own grant, so an org admin may change their
role or remove them. The last admin can never be removed or demoted. Every change is audited."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import Membership, User, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, tenant_repo

router = APIRouter()


class OrgMembershipOut(BaseModel):
    user_id: int
    email: str
    name: str | None
    github_login: str | None
    role: Literal["member", "admin"]
    # "github": mirrors GitHub org status, read-only here. "invite": granted by an accepted invitation.
    source: str
    editable: bool


class OrgMembershipRoleIn(BaseModel):
    role: Literal["member", "admin"]


def _out(membership: Membership, user: User) -> OrgMembershipOut:
    return OrgMembershipOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        github_login=user.github_login,
        role=membership.role,
        source=membership.source,
        editable=membership.source == "invite",
    )


def _lock_invite_membership(db: Session, ctx: OrgContext, user_id: int) -> Membership:
    """The invite-sourced membership to change, row-locked (404 if none; 409 if GitHub-managed)."""
    membership = tenant_repo.get_membership(db, ctx.org.tenant_id, user_id, for_update=True)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if membership.source != "invite":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This member is managed by GitHub: change their role or membership in the GitHub organization",
        )
    return membership


def _refuse_if_last_admin(db: Session, ctx: OrgContext, membership: Membership) -> None:
    """Block changes that would leave the org with no admin. The admin rows are locked so two concurrent
    removals/demotions cannot both pass the check."""
    if membership.role != "admin":
        return
    if tenant_repo.count_admins_locked(db, ctx.org.tenant_id) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An organization needs at least one admin")


@router.get("/orgs/{org_login}/members", response_model=list[OrgMembershipOut])
def list_org_memberships(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(Membership, User)
        .join(User, User.id == Membership.user_id)
        .filter(Membership.tenant_id == ctx.org.tenant_id)
        .order_by(Membership.created_at, Membership.id)
        .all()
    )
    return [_out(m, u) for m, u in rows]


@router.put("/orgs/{org_login}/members/{user_id}", response_model=OrgMembershipOut)
def update_org_membership(
    user_id: int,
    body: OrgMembershipRoleIn,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    membership = _lock_invite_membership(db, ctx, user_id)
    previous = membership.role
    if previous != body.role:
        if body.role == "member":
            _refuse_if_last_admin(db, ctx, membership)
        tenant_repo.update_membership_role(db, ctx.org.tenant_id, user_id, body.role, commit=False)
        audit_repo.write(
            db, actor=user.email, action="membership.update", target=ctx.org.github_login,
            payload={"user_id": user_id, "previous_role": previous, "role": body.role, "source": "invite"},
            tenant_id=ctx.org.tenant_id, commit=False,
        )
    db.commit()
    target = db.get(User, user_id)
    return _out(membership, target)


@router.delete("/orgs/{org_login}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_org_membership(
    user_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    membership = _lock_invite_membership(db, ctx, user_id)
    _refuse_if_last_admin(db, ctx, membership)
    role = membership.role
    tenant_repo.delete_membership(db, ctx.org.tenant_id, user_id, commit=False)
    audit_repo.write(
        db, actor=user.email, action="membership.remove", target=ctx.org.github_login,
        payload={"user_id": user_id, "previous_role": role, "source": "invite"},
        tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
