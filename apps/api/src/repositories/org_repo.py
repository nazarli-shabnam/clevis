import logging

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.db import GitHubInstallation, Org, ScanResult
from src.repositories import tenant_repo

logger = logging.getLogger(__name__)


class OrgIdentityMismatch(Exception):
    """A login matches an existing Org whose stored github_org_id is a different GitHub org: the
    login was deleted/renamed away and claimed by someone else."""


def ensure_tenant_linked(db: Session, org: Org) -> Org:
    if org.tenant_id is not None:
        return org
    tenant = tenant_repo.get_or_create_org_tenant(db, org.id)
    # RLS WITH CHECK requires tenant_id = app.tenant_id, which no caller could have set for a
    # brand-new tenant. SET LOCAL (not plain SET) so no tenant context leaks past this commit.
    db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant.id)}"))
    org.tenant_id = tenant.id
    db.commit()
    db.refresh(org)
    return org


def get_by_login(db: Session, github_login: str) -> Org | None:
    return db.query(Org).filter(Org.github_login == github_login).first()


def get_by_login_ci(db: Session, github_login: str) -> Org | None:
    # Case-insensitive: callers pass arbitrary user-typed logins, and a casing variant of a
    # connected org must still resolve to it rather than fall through to personal resolution.
    return db.query(Org).filter(func.lower(Org.github_login) == github_login.lower()).first()


def get_by_id(db: Session, org_id: int) -> Org | None:
    return db.query(Org).filter(Org.id == org_id).first()


def get_by_org_id(db: Session, github_org_id: int) -> Org | None:
    return db.query(Org).filter(Org.github_org_id == github_org_id).first()


def _refuse_other_orgs_row(org: Org, github_login: str, github_org_id: int | None) -> None:
    if github_org_id is not None and org.github_org_id is not None and org.github_org_id != github_org_id:
        # Reusing the row would hand the old org's tenant (members, events, alerts, scans) to
        # whoever GitHub now reports as admin of this login.
        logger.warning(
            "refusing to reuse org %r (id %d): login now belongs to GitHub org id %d",
            github_login, org.id, github_org_id,
        )
        raise OrgIdentityMismatch(github_login)


def _apply_rename(db: Session, org: Org, new_login: str) -> None:
    """Move everything keyed by the org's login string over to its new GitHub login, atomically.

    Installation lookups (token resolution) match ``account_login`` and scan history, export, digest
    and the public badge match ``scan_results.owner``, so leaving either on the old login orphans
    them after a rename. Scans an org member ran from their personal tenant keep the old owner."""
    old_login = org.github_login
    org.github_login = new_login
    if org.tenant_id is not None:
        # Both tables' RLS WITH CHECK needs app.tenant_id = the org's tenant. SET LOCAL so the
        # context ends with this commit (same pattern as ensure_tenant_linked).
        db.execute(text(f"SET LOCAL app.tenant_id = {int(org.tenant_id)}"))
        db.query(GitHubInstallation).filter(GitHubInstallation.org_id == org.id).update(
            {GitHubInstallation.account_login: new_login, GitHubInstallation.token_ref: f"tok_{new_login}"},
            synchronize_session=False,
        )
        db.query(ScanResult).filter(
            ScanResult.tenant_id == org.tenant_id, func.lower(ScanResult.owner) == old_login.lower()
        ).update({ScanResult.owner: new_login}, synchronize_session=False)
    db.commit()
    db.refresh(org)


def get_or_create(db: Session, github_login: str, github_org_id: int | None = None) -> Org:
    # Case-insensitive: GitHub logins are, and an exact-case lookup would miss a casing variant of a
    # connected org and insert a duplicate instead of reaching the identity check below.
    org = get_by_login_ci(db, github_login)
    if org is not None:
        _refuse_other_orgs_row(org, github_login, github_org_id)
        if github_org_id is not None and org.github_org_id is None:
            org.github_org_id = github_org_id
            db.commit()
            db.refresh(org)
        return ensure_tenant_linked(db, org)

    if github_org_id is not None:
        # The org may have been renamed on GitHub; github_org_id is the stable identity, so
        # update the login in place.
        org = get_by_org_id(db, github_org_id)
        if org is not None:
            if org.github_login != github_login:
                org = ensure_tenant_linked(db, org)
                _apply_rename(db, org, github_login)
            return ensure_tenant_linked(db, org)

    org = Org(github_login=github_login, github_org_id=github_org_id)
    db.add(org)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent insert of the same github_login or github_org_id.
        db.rollback()
        org = get_by_login_ci(db, github_login)
        if org is not None:
            _refuse_other_orgs_row(org, github_login, github_org_id)
        elif github_org_id is not None:
            org = get_by_org_id(db, github_org_id)
        if org is None:
            raise
        return ensure_tenant_linked(db, org)
    db.refresh(org)
    return ensure_tenant_linked(db, org)
