import logging
from datetime import datetime, timezone

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from src.core.db import GitHubInstallation, set_session_tenant
from src.repositories import tenant_repo

logger = logging.getLogger(__name__)


# pg_advisory_xact_lock(namespace, tenant_id): serializes installation writes for a tenant
# with the uninstall purge, so a new installation can't commit between the purge's
# "no installation left?" check and its deletes.
_TENANT_INSTALL_LOCK_NS = 727101


def _lock_tenant_installs(db: Session, tenant_id: int) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :t)"), {"ns": _TENANT_INSTALL_LOCK_NS, "t": int(tenant_id)}
    )


def _resolve_tenant_id(db: Session, org_id: int | None, owner_user_id: int | None) -> int:
    if org_id is not None:
        return tenant_repo.get_or_create_org_tenant(db, org_id).id
    return tenant_repo.ensure_personal_tenant(db, owner_user_id).id


def upsert(
    db: Session,
    account_login: str,
    account_type: str,
    auth_mode: str,
    installation_id: int | None = None,
    org_id: int | None = None,
    owner_user_id: int | None = None,
) -> GitHubInstallation:
    if (org_id is None) == (owner_user_id is None):
        raise ValueError("Exactly one of org_id or owner_user_id must be set")

    query = db.query(GitHubInstallation).filter(GitHubInstallation.account_login == account_login)
    if org_id is not None:
        query = query.filter(GitHubInstallation.org_id == org_id)
    else:
        query = query.filter(GitHubInstallation.owner_user_id == owner_user_id)

    token_ref = f"tok_{account_login}"
    tenant_id = _resolve_tenant_id(db, org_id, owner_user_id)
    _lock_tenant_installs(db, tenant_id)
    existing = query.first()
    if existing:
        existing.account_type = account_type
        existing.auth_mode = auth_mode
        if installation_id is not None:
            # A re-sync without an id (e.g. the org/personal sync routes) must not wipe the
            # one GitHub gave us -- token minting and webhook tenant resolution depend on it.
            existing.installation_id = installation_id
        existing.token_ref = token_ref
        existing.tenant_id = tenant_id
        db.commit()
        db.refresh(existing)
        return existing

    row = GitHubInstallation(
        account_login=account_login,
        account_type=account_type,
        installation_id=installation_id,
        auth_mode=auth_mode,
        token_ref=token_ref,
        org_id=org_id,
        owner_user_id=owner_user_id,
        tenant_id=tenant_id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent sync (unique constraint); update the row it just inserted.
        db.rollback()
        _lock_tenant_installs(db, tenant_id)
        existing = query.first()
        if existing is None:
            raise
        existing.account_type = account_type
        existing.auth_mode = auth_mode
        if installation_id is not None:
            # A re-sync without an id (e.g. the org/personal sync routes) must not wipe the
            # one GitHub gave us -- token minting and webhook tenant resolution depend on it.
            existing.installation_id = installation_id
        existing.token_ref = token_ref
        existing.tenant_id = tenant_id
        db.commit()
        db.refresh(existing)
        return existing
    db.refresh(row)
    return row


def create(
    db: Session,
    account_login: str,
    account_type: str,
    auth_mode: str,
    installation_id: int | None = None,
    org_id: int | None = None,
    owner_user_id: int | None = None,
) -> GitHubInstallation:
    return upsert(
        db,
        account_login=account_login,
        account_type=account_type,
        auth_mode=auth_mode,
        installation_id=installation_id,
        org_id=org_id,
        owner_user_id=owner_user_id,
    )


def get_for_org(db: Session, org_id: int, account_login: str) -> GitHubInstallation | None:
    # Case-insensitive: account_login is stored verbatim from GitHub, and the RBAC/ownership checks
    # elsewhere compare case-insensitively, so an exact match could miss a real installation.
    return (
        db.query(GitHubInstallation)
        .filter(
            GitHubInstallation.org_id == org_id,
            func.lower(GitHubInstallation.account_login) == account_login.lower(),
        )
        .first()
    )


def get_for_user(db: Session, owner_user_id: int, account_login: str) -> GitHubInstallation | None:
    return (
        db.query(GitHubInstallation)
        .filter(
            GitHubInstallation.owner_user_id == owner_user_id,
            func.lower(GitHubInstallation.account_login) == account_login.lower(),
        )
        .first()
    )


def get_by_installation_id_for_org(db: Session, org_id: int, installation_id: int) -> GitHubInstallation | None:
    """Lookup scoped to this org, so an org admin can't disconnect another tenant's installation by id."""
    return (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.org_id == org_id, GitHubInstallation.installation_id == installation_id)
        .first()
    )


def get_by_installation_id_for_user(db: Session, owner_user_id: int, installation_id: int) -> GitHubInstallation | None:
    """Same contract as get_by_installation_id_for_org, for personal-installation disconnect."""
    return (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.owner_user_id == owner_user_id, GitHubInstallation.installation_id == installation_id)
        .first()
    )


def list_for_org(db: Session, org_id: int) -> list[GitHubInstallation]:
    return (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.org_id == org_id)
        .order_by(GitHubInstallation.created_at.desc())
        .all()
    )


def list_for_user(db: Session, owner_user_id: int) -> list[GitHubInstallation]:
    return (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.owner_user_id == owner_user_id)
        .order_by(GitHubInstallation.created_at.desc())
        .all()
    )


def delete_by_installation_id(db: Session, installation_id: int) -> tuple[int, int | None]:
    """Remove every row referencing a GitHub installation_id (e.g. on uninstall).

    Returns (rows deleted, tenant_id of the first deleted row) for audit attribution. Called from
    the unauthenticated webhook receiver, so the tenant is resolved first via the SECURITY DEFINER
    resolve_installation_tenant_id() and set as session context; otherwise RLS hides every row."""
    tenant_id = db.execute(
        text("SELECT resolve_installation_tenant_id(:installation_id)"), {"installation_id": installation_id}
    ).scalar()
    if tenant_id is not None:
        set_session_tenant(db, tenant_id)

    rows = db.query(GitHubInstallation).filter(GitHubInstallation.installation_id == installation_id).all()
    resolved_tenant_id = rows[0].tenant_id if rows else tenant_id
    count = db.query(GitHubInstallation).filter(GitHubInstallation.installation_id == installation_id).delete()
    db.commit()
    if resolved_tenant_id is not None:
        purge_tenant_github_data_if_disconnected(db, resolved_tenant_id)
    return count, resolved_tenant_id


# GitHub-derived, per-tenant data that's meaningless once no installation covers the tenant.
_TENANT_GITHUB_TABLES = (
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "activity_sync_cursors",
    "org_membership_sync_cursors",
    "automation_repo_settings",
    "repo_event_daily_counts",
    "repo_events",
)


def purge_tenant_github_data_if_disconnected(db: Session, tenant_id: int) -> None:
    """Delete a tenant's GitHub-derived rows once its last installation is gone.

    Each table runs in its own savepoint: under the restricted clevis_api role some tables
    grant no DELETE, and a missing privilege there must not fail the uninstall itself."""
    set_session_tenant(db, tenant_id)
    # Check-then-delete runs under the tenant lock upsert() also takes, in one transaction.
    _lock_tenant_installs(db, tenant_id)
    if db.query(GitHubInstallation).filter(GitHubInstallation.tenant_id == tenant_id).first() is not None:
        db.commit()  # release the lock
        return
    for table in _TENANT_GITHUB_TABLES:
        try:
            with db.begin_nested():
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant_id})
        except ProgrammingError:
            logger.warning("could not purge %s for tenant %s after uninstall (no DELETE privilege)", table, tenant_id)
    db.commit()


def update_permissions(
    db: Session, *, installation_id: int, permissions: dict, synced_at: datetime | None = None
) -> tuple[int, bool]:
    """Record GitHub's installation `permissions` object on every row for `installation_id`.

    Returns `(rows_updated, changed)`: `rows_updated` is 0 if the installation isn't connected;
    `changed` lets the webhook handler skip a duplicate audit row on redelivery. `synced_at` is
    bumped either way. Resolves the tenant for RLS the same way as delete_by_installation_id.
    Updating an already-connected row is safe from the webhook; only creating rows would cross
    a trust boundary. `with_for_update()` keeps concurrent redeliveries from both seeing
    `changed=True`.
    """
    tenant_id = db.execute(
        text("SELECT resolve_installation_tenant_id(:installation_id)"), {"installation_id": installation_id}
    ).scalar()
    if tenant_id is not None:
        set_session_tenant(db, tenant_id)

    rows = (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.installation_id == installation_id)
        .with_for_update()
        .all()
    )
    changed = any(r.granted_permissions != permissions for r in rows)

    count = (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.installation_id == installation_id)
        .update(
            {
                GitHubInstallation.granted_permissions: permissions,
                GitHubInstallation.permissions_synced_at: synced_at or datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return count, changed
