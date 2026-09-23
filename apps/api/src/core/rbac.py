"""Org-scoped RBAC dependencies.

Roles are resolved from the DB per request, since membership can change while a JWT is still valid.
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import Membership, Org, Tenant, get_db
from src.repositories import org_repo, tenant_repo

_ROLE_RANK = {"member": 0, "admin": 1}


@dataclass
class OrgContext:
    org: Org
    membership: Membership


@dataclass
class PersonalTenantContext:
    tenant: Tenant


def set_tenant_session_context(db: Session, tenant_id: int, user_id: int) -> None:
    # Plain SET, not SET LOCAL: a request can commit more than once. Read by the RLS policies.
    # SET can't take bind params; both values are int PKs, so formatting them in is safe.
    db.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))
    db.execute(text(f"SET app.user_id = {int(user_id)}"))


def audit_tenant(db: Session, user_id: int, owner: str) -> int:
    """Resolve, and set as session context, the tenant for a ``/me/repos/{owner}/...`` audit row.

    The owner's org tenant if the caller is a member, else the caller's personal tenant. Never
    ``None``: audit_logs RLS is strict equality, so a NULL-tenant insert would fail.
    """
    org = org_repo.get_by_login_ci(db, owner)
    if org is not None:
        org = org_repo.ensure_tenant_linked(db, org)
        if tenant_repo.get_membership(db, org.tenant_id, user_id) is not None:
            set_tenant_session_context(db, org.tenant_id, user_id)
            return org.tenant_id
    tenant_id = tenant_repo.ensure_personal_tenant(db, user_id).id
    set_tenant_session_context(db, tenant_id, user_id)
    return tenant_id


def resolve_org_role(db: Session, org_login: str, user_id: int, min_role: Literal["member", "admin"]) -> OrgContext | None:
    """Non-raising form of require_org_role's check: None on missing org or insufficient role.

    Shared with installations.sync_org_installation so the two can't drift. Case-insensitive,
    since GitHub logins are unique regardless of case.
    """
    org = org_repo.get_by_login_ci(db, org_login)
    if org is None:
        return None
    # tenant_id is nullable on legacy rows; self-heal it before the role lookup reads `memberships`.
    org = org_repo.ensure_tenant_linked(db, org)
    membership = tenant_repo.get_membership(db, org.tenant_id, user_id)
    if membership is None or _ROLE_RANK.get(membership.role, -1) < _ROLE_RANK[min_role]:
        return None
    set_tenant_session_context(db, org.tenant_id, user_id)
    return OrgContext(org=org, membership=membership)


def require_org_role(min_role: Literal["member", "admin"]):
    """Dependency factory: 404 if org_login (path param) doesn't exist, 403 if the
    current user isn't a member of it or is below min_role."""

    def dependency(
        org_login: str,
        db: Session = Depends(get_db),
        user: UserOut = Depends(require_auth),
    ) -> OrgContext:
        ctx = resolve_org_role(db, org_login, user.id, min_role)
        if ctx is not None:
            return ctx
        if org_repo.get_by_login_ci(db, org_login) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"'{org_login}' isn't connected to Clevis yet — sign in with GitHub or "
                    "install the GitHub App for this org first. A pasted personal access token "
                    "connects the org only if it has read:org scope and GitHub confirms you "
                    "administer it."
                ),
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Org access required")

    return dependency


def require_personal_tenant(
    db: Session = Depends(get_db),
    user: UserOut = Depends(require_auth),
) -> PersonalTenantContext:
    """Dependency for routes scoped to the caller's own personal tenant.

    Not for /me/... routes whose `owner` can resolve to an org tenant (see resolve_owner_token);
    wiring those is deferred."""
    tenant = tenant_repo.ensure_personal_tenant(db, user.id)
    set_tenant_session_context(db, tenant.id, user.id)
    return PersonalTenantContext(tenant=tenant)


def assert_owner_matches_org(owner: str, ctx: OrgContext) -> None:
    """Raises 403 if a repo-level `owner` path/body value doesn't match the org context
    require_org_role already resolved — keeps an org-scoped route from acting on a
    GitHub owner outside the org the caller was authorized for."""
    if owner.lower() != ctx.org.github_login.lower():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner must match the org in the URL")
