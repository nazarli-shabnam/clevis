from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.db import Membership, Org, Tenant


def _set_session_user(db: Session, user_id: int) -> None:
    # memberships RLS allows a row when tenant_id OR user_id matches the session. Every write here
    # targets a known user_id, so self-identifying via SET LOCAL app.user_id is never an escalation
    # and lets writes succeed before any tenant context exists. Scoped to the transaction.
    db.execute(text(f"SET LOCAL app.user_id = {int(user_id)}"))


def _persist_new(db: Session, obj, refetch, *, commit: bool):
    """Insert ``obj``, tolerating a lost race with a concurrent insert of the same row.

    ``commit=True``: commit; on unique-violation, full rollback then ``refetch()``.
    ``commit=False``: the caller owns the transaction (and possibly a FOR UPDATE lock), so the
    insert goes through a SAVEPOINT and only that is rolled back before ``refetch()``.
    """
    if commit:
        db.add(obj)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = refetch()
            if existing is None:
                raise
            return existing
        db.refresh(obj)
        return obj

    try:
        with db.begin_nested():
            db.add(obj)
            db.flush()
    except IntegrityError:
        existing = refetch()
        if existing is None:
            raise
        return existing
    return obj


def get_org_tenant(db: Session, org_id: int) -> Tenant | None:
    """Read-only lookup of an org's tenant, or None if the org has no tenant row yet."""
    return db.query(Tenant).filter(Tenant.kind == "org", Tenant.org_id == org_id).first()


def get_or_create_org_tenant(db: Session, org_id: int, *, commit: bool = True) -> Tenant:
    tenant = get_org_tenant(db, org_id)
    if tenant is not None:
        return tenant
    return _persist_new(
        db, Tenant(kind="org", org_id=org_id), lambda: get_org_tenant(db, org_id), commit=commit
    )


def ensure_personal_tenant(db: Session, user_id: int, commit: bool = True) -> Tenant:
    """Get-or-create a user's personal tenant plus its admin self-membership, atomically.

    commit=False is for new-user registration: the tenant and membership land in the same
    transaction as the flushed User row, so a failure can't leave a User with no personal tenant.
    Race handling is skipped in that mode since the user_id is brand new."""
    tenant = db.query(Tenant).filter(Tenant.kind == "personal", Tenant.personal_user_id == user_id).first()
    if tenant is None:
        tenant = Tenant(kind="personal", personal_user_id=user_id)
        db.add(tenant)
        if commit:
            try:
                db.commit()
            except IntegrityError:
                # Lost a race with a concurrent insert of the same user's personal tenant.
                db.rollback()
                tenant = db.query(Tenant).filter(Tenant.kind == "personal", Tenant.personal_user_id == user_id).first()
                if tenant is None:
                    raise
            else:
                db.refresh(tenant)
        else:
            db.flush()

    if commit:
        get_or_create_membership(db, tenant_id=tenant.id, user_id=user_id, role="admin")
    else:
        # Query first rather than insert unconditionally, so a commit=False caller reusing an
        # existing user_id degrades to a no-op instead of an IntegrityError.
        existing_membership = (
            db.query(Membership).filter(Membership.tenant_id == tenant.id, Membership.user_id == user_id).first()
        )
        if existing_membership is None:
            _set_session_user(db, user_id)
            db.add(Membership(tenant_id=tenant.id, user_id=user_id, role="admin"))
            db.flush()
    return tenant


def get_membership(
    db: Session, tenant_id: int, user_id: int, *, for_update: bool = False
) -> Membership | None:
    """Lookup keyed on tenant_id, the source of truth for org RBAC.

    for_update takes a row lock, used by org_membership_repo to serialize concurrent grant/revoke."""
    query = db.query(Membership).filter(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    return query.first()


def list_org_memberships_for_user(db: Session, user_id: int) -> list[tuple[Org, Membership]]:
    """All of a user's org-tenant memberships, joined back to each Org row -- for callers
    that need to enumerate a user's orgs rather than check one specific org."""
    return (
        db.query(Org, Membership)
        .join(Tenant, Tenant.org_id == Org.id)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .filter(Membership.user_id == user_id, Tenant.kind == "org")
        .all()
    )


def get_or_create_membership(
    db: Session, tenant_id: int, user_id: int, role: str, *, commit: bool = True, source: str = "github"
) -> Membership:
    def _find():
        # Re-assert the session user on every lookup: the post-IntegrityError refetch runs after a
        # rollback that discarded SET LOCAL, and a context-less read under FORCE RLS returns nothing.
        _set_session_user(db, user_id)
        return (
            db.query(Membership)
            .filter(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
            .first()
        )

    membership = _find()
    if membership is not None:
        return membership
    return _persist_new(db, Membership(tenant_id=tenant_id, user_id=user_id, role=role, source=source), _find, commit=commit)


def upsert_membership(
    db: Session, tenant_id: int, user_id: int, role: str, *, commit: bool = True, source: str = "github"
) -> Membership:
    """get_or_create_membership plus fixing a stale role on an existing row.

    commit=False keeps the whole sequence in the caller's transaction."""
    membership = get_or_create_membership(db, tenant_id, user_id, role, commit=commit, source=source)
    if membership.role != role:
        updated = update_membership_role(db, tenant_id, user_id, role, commit=commit)
        # A concurrent delete_membership could remove the row in between; re-create it.
        membership = (
            updated
            if updated is not None
            else get_or_create_membership(db, tenant_id, user_id, role, commit=commit, source=source)
        )
    return membership


def update_membership_role(
    db: Session, tenant_id: int, user_id: int, role: str, *, commit: bool = True
) -> Membership | None:
    _set_session_user(db, user_id)
    membership = (
        db.query(Membership)
        .filter(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
        .first()
    )
    if membership is None:
        return None
    membership.role = role
    # commit=False: flush into the caller's transaction so its FOR UPDATE lock holds until the
    # caller's single commit. No refresh needed; the value is current in-session.
    if commit:
        db.commit()
        db.refresh(membership)
    else:
        db.flush()
    return membership


def delete_membership(db: Session, tenant_id: int, user_id: int, *, commit: bool = True) -> None:
    _set_session_user(db, user_id)
    db.query(Membership).filter(
        Membership.tenant_id == tenant_id, Membership.user_id == user_id
    ).delete()
    # commit=False: see update_membership_role.
    if commit:
        db.commit()
    else:
        db.flush()
