"""
Org-scoped RBAC dependencies.

Unlike require_auth/require_workspace_admin (JWT-only, no DB hit), these dependencies
resolve role fresh from the DB on every request, because org membership and invite
status can change while a 30-day JWT is still valid.
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import Org, OrgMembership, get_db
from src.repositories import org_membership_repo, org_repo

_ROLE_RANK = {"member": 0, "admin": 1}


@dataclass
class OrgContext:
    org: Org
    membership: OrgMembership


def require_org_role(min_role: Literal["member", "admin"]):
    """Dependency factory: 404 if org_login (path param) doesn't exist, 403 if the
    current user isn't a member of it or is below min_role."""

    def dependency(
        org_login: str,
        db: Session = Depends(get_db),
        user: UserOut = Depends(require_auth),
    ) -> OrgContext:
        org = org_repo.get_by_login(db, org_login)
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        membership = org_membership_repo.get(db, org.id, user.id)
        if membership is None or _ROLE_RANK.get(membership.role, -1) < _ROLE_RANK[min_role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Org access required")
        return OrgContext(org=org, membership=membership)

    return dependency


def assert_owner_matches_org(owner: str, ctx: OrgContext) -> None:
    """Raises 403 if a repo-level `owner` path/body value doesn't match the org context
    require_org_role already resolved — keeps an org-scoped route from acting on a
    GitHub owner outside the org the caller was authorized for."""
    if owner != ctx.org.github_login:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner must match the org in the URL")
