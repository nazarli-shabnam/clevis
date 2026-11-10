"""Org membership and collaborator data Clevis already stores, shaped for the identity checks.

``org_members.two_factor_enabled`` (reconciled by the worker) and ``repo_collaborators`` (from webhooks and the
same reconcile) feed ``checks.identity_checks``, which make no GitHub calls. Read on the request thread before
a scan starts and passed in as plain dicts: a scan runs on worker threads and must not share the DB session.
"""

from sqlalchemy.orm import Session

from src.core.db import OrgMember, RepoCollaborator, set_session_tenant


def load_stored_identity(db: Session, tenant_id: int) -> dict:
    """``{"members": [...], "collaborators": [...]}`` for one org tenant."""
    set_session_tenant(db, tenant_id)  # these tables are tenant-isolated
    members = db.query(OrgMember.login, OrgMember.two_factor_enabled).filter(OrgMember.tenant_id == tenant_id).all()
    collaborators = (
        db.query(
            RepoCollaborator.repo, RepoCollaborator.login, RepoCollaborator.permission,
            RepoCollaborator.is_outside_collaborator,
        )
        .filter(RepoCollaborator.tenant_id == tenant_id)
        .all()
    )
    return {
        "members": [{"login": m.login, "two_factor_enabled": m.two_factor_enabled} for m in members],
        "collaborators": [
            {
                "repo": c.repo, "login": c.login, "permission": c.permission,
                "is_outside_collaborator": c.is_outside_collaborator,
            }
            for c in collaborators
        ],
    }
