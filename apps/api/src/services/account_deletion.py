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


# Cleared by the purge function above, or handled separately (memberships; tenants/orgs form a foreign-key cycle).
_SKIPPED = frozenset({
    "tenants", "orgs", "memberships", "github_installations",
    "security_alerts", "org_members", "repo_collaborators", "activity_sync_cursors", "org_membership_sync_cursors",
    "automation_repo_settings", "repo_event_daily_counts", "repo_events", "security_alert_syncs",
})


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
        db.execute(text("DELETE FROM memberships WHERE tenant_id = :t AND user_id = :u"), {"t": org.tenant_id, "u": user_id})

    # Invitations they sent and scans they ran stay behind in orgs they already left (leaving deletes only the
    # membership row), and would block the final user delete on the foreign keys, so clear every org tenant.
    for (tenant_id,) in db.execute(text("SELECT id FROM tenants WHERE kind = 'org'")).all():
        _in_tenant(db, tenant_id)
        db.execute(text("DELETE FROM invitations WHERE tenant_id = :t AND invited_by_user_id = :u"), {"t": tenant_id, "u": user_id})
        db.execute(update(ScanResult).where(ScanResult.tenant_id == tenant_id, ScanResult.scanned_by_user_id == user_id).values(scanned_by_user_id=None))

    # The personal tenant and every row stored under it: children before parents.
    _in_tenant(db, personal_id)
    db.execute(text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": personal_id})
    # clevis_api holds no DELETE on the GitHub-derived tables: they go through the narrow SECURITY DEFINER purge
    # (migrations 0069/0080), which only acts once the tenant has no installation left.
    db.execute(text("DELETE FROM github_installations WHERE tenant_id = :t"), {"t": personal_id})
    db.execute(text("SELECT purge_tenant_github_data(:t)"), {"t": personal_id})
    with warnings.catch_warnings():
        # The orgs/tenants foreign-key cycle (both skipped below) makes SQLAlchemy warn about the sort order.
        warnings.simplefilter("ignore")
        ordered = list(reversed(Base.metadata.sorted_tables))
    for table in ordered:
        if table.name in _SKIPPED or "tenant_id" not in table.c:
            continue
        db.execute(table.delete().where(table.c.tenant_id == personal_id))
    db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": personal_id})
    db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
