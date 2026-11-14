"""Delete a user's own account.

What goes: the user row, their sessions, their memberships in every org, the invitations they sent, and their
*personal* tenant with everything stored under it (personal installations, scans, jobs, and the ``auth.*`` audit
rows written there). What stays: the audit rows written in organizations (their actor is just an email string, so
the org's history still reads correctly), and org scans they ran, which lose only the link to the user.
Each org they leave gets an ``account.deleted`` audit row so its admins can see why a member disappeared.

Refused (``AccountDeletionRefused``) while it would strand something: the workspace admin (nobody else can be
promoted) and the only admin of an organization must hand over first.
"""

import warnings

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from src.core.db import Base, Membership, ScanResult, User
from src.repositories import audit_repo, tenant_repo


class AccountDeletionRefused(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def blockers(db: Session, user: User) -> list[str]:
    reasons: list[str] = []
    if user.is_workspace_admin:
        reasons.append("You are the workspace admin; deleting this account would leave the instance without one")
    for org, membership in tenant_repo.list_org_memberships_for_user(db, user.id):
        if membership.role != "admin":
            continue
        admins = len(
            db.query(Membership.id)
            .filter(Membership.tenant_id == org.tenant_id, Membership.role == "admin")
            .with_for_update()
            .all()
        )
        if admins <= 1:
            reasons.append(f"You are the only admin of {org.github_login}; make someone else an admin first")
    return reasons


def _in_tenant(db: Session, tenant_id: int) -> None:
    db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))


def delete_account(db: Session, user: User) -> None:
    """Does everything in the caller's transaction and does not commit; the caller commits (or rolls back on
    any error, so nothing is half-deleted). Raises ``AccountDeletionRefused``."""
    reasons = blockers(db, user)
    if reasons:
        raise AccountDeletionRefused(reasons)
    user_id, email = user.id, user.email

    personal = tenant_repo.ensure_personal_tenant(db, user_id, commit=False)
    personal_id = personal.id
    for org, membership in tenant_repo.list_org_memberships_for_user(db, user_id):
        _in_tenant(db, org.tenant_id)
        audit_repo.write(
            db, email, "account.deleted", org.github_login,
            {"user_id": user_id, "previous_role": membership.role, "source": membership.source},
            tenant_id=org.tenant_id, commit=False,
        )
        db.execute(text("DELETE FROM invitations WHERE tenant_id = :t AND invited_by_user_id = :u"), {"t": org.tenant_id, "u": user_id})
        db.execute(update(ScanResult).where(ScanResult.tenant_id == org.tenant_id, ScanResult.scanned_by_user_id == user_id).values(scanned_by_user_id=None))
        db.execute(text("DELETE FROM memberships WHERE tenant_id = :t AND user_id = :u"), {"t": org.tenant_id, "u": user_id})

    # The personal tenant and every row stored under it: children before parents.
    _in_tenant(db, personal_id)
    db.execute(text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": personal_id})
    with warnings.catch_warnings():
        # The orgs/tenants foreign-key cycle (both skipped below) makes SQLAlchemy warn about the sort order.
        warnings.simplefilter("ignore")
        ordered = list(reversed(Base.metadata.sorted_tables))
    for table in ordered:
        if table.name in ("tenants", "orgs", "memberships") or "tenant_id" not in table.c:
            continue
        db.execute(table.delete().where(table.c.tenant_id == personal_id))
    db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": personal_id})
    db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
