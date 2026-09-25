from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import Membership
from src.repositories import tenant_repo

# Thin org_id-keyed adapter over tenant_repo's tenant-scoped memberships.
#
# Writes lock the memberships row (FOR UPDATE) and commit exactly once, so a concurrent grant
# and revoke for the same (org, user) stay serialized. Each write sets app.user_id first
# (SET LOCAL); under FORCE RLS the lock query would otherwise match zero rows.
#
# Reads for an org with no tenant row return empty; only get_or_create creates the tenant.


def _set_session_user(db: Session, user_id: int) -> None:
    db.execute(text(f"SET LOCAL app.user_id = {int(user_id)}"))


def get(db: Session, org_id: int, user_id: int) -> Membership | None:
    tenant = tenant_repo.get_org_tenant(db, org_id)
    if tenant is None:
        return None
    return tenant_repo.get_membership(db, tenant.id, user_id)


def get_or_create(db: Session, org_id: int, user_id: int, role: str, source: str = "github") -> Membership:
    """``source`` only applies to a newly created row; an existing row keeps its source."""
    _set_session_user(db, user_id)
    tenant_id = tenant_repo.get_or_create_org_tenant(db, org_id, commit=False).id
    # Lock the row if it exists so a concurrent delete() can't land before our commit. A lost
    # insert race is handled by upsert_membership's SAVEPOINT recovery; the call is one transaction.
    tenant_repo.get_membership(db, tenant_id, user_id, for_update=True)
    membership = tenant_repo.upsert_membership(
        db, tenant_id=tenant_id, user_id=user_id, role=role, commit=False, source=source
    )
    db.commit()
    return membership


def create_if_missing(db: Session, org_id: int, user_id: int, role: str, source: str) -> Membership:
    """Like get_or_create, but never changes an existing row's role -- including one a
    concurrent transaction inserted first (e.g. a GitHub admin grant racing an invite accept)."""
    _set_session_user(db, user_id)
    tenant_id = tenant_repo.get_or_create_org_tenant(db, org_id, commit=False).id
    membership = tenant_repo.get_or_create_membership(
        db, tenant_id=tenant_id, user_id=user_id, role=role, commit=False, source=source
    )
    db.commit()
    return membership


def update_role(db: Session, org_id: int, user_id: int, role: str) -> Membership | None:
    tenant = tenant_repo.get_org_tenant(db, org_id)
    if tenant is None:
        return None
    _set_session_user(db, user_id)
    if tenant_repo.get_membership(db, tenant.id, user_id, for_update=True) is None:
        db.commit()  # close the read transaction cleanly; nothing to update
        return None
    tenant_repo.update_membership_role(db, tenant_id=tenant.id, user_id=user_id, role=role, commit=False)
    db.commit()
    # Not a refresh: the commit released the lock, so a concurrent delete() may have removed the
    # row. Re-query (re-establishing context the commit cleared) so that returns None.
    _set_session_user(db, user_id)
    return tenant_repo.get_membership(db, tenant.id, user_id)


def delete(db: Session, org_id: int, user_id: int) -> None:
    tenant = tenant_repo.get_org_tenant(db, org_id)
    if tenant is None:
        return
    _set_session_user(db, user_id)
    # Lock (if present) then delete + commit as one operation, so a concurrent
    # get_or_create() blocks on the row until this finishes rather than racing it.
    tenant_repo.get_membership(db, tenant.id, user_id, for_update=True)
    tenant_repo.delete_membership(db, tenant_id=tenant.id, user_id=user_id, commit=False)
    db.commit()
