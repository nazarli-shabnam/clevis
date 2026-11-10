"""The saved-PAT fallback in token_resolution (#792): the server uses a workspace admin's saved token itself, so
the browser no longer fetches the plaintext (and `token.resolve` is only audited for a deliberate reveal)."""

import pytest
from sqlalchemy import text

from src.core._crypto import encrypt_job_token
from src.core.config import settings
from src.core.db import AuditLog, SavedToken, User, set_session_user
from src.repositories import org_membership_repo, org_repo
from src.services.token_resolution import (
    InsufficientOrgRole,
    NoGitHubTokenAvailable,
    resolve_org_token,
    resolve_owner_token,
    resolve_personal_token,
)


def _user(db, email: str, *, admin: bool) -> User:
    u = User(email=email, name=None, password_hash=None, is_workspace_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _save(db, org: str = "Acme", token: str = "ghp_saved") -> None:
    db.add(SavedToken(org=org, label="t", encrypted_token=encrypt_job_token(token, settings.job_secret_key.get_secret_value())))
    db.commit()


@pytest.fixture()
def acme(db):
    org = org_repo.get_or_create(db, github_login="acme")
    admin = _user(db, "root@e.com", admin=True)
    member = _user(db, "member@e.com", admin=False)
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def test_workspace_admin_gets_the_saved_token_when_nothing_else_is_available(db, acme):
    _save(db)  # saved under a different case than the org login
    set_session_user(db, acme["admin"].id)
    assert resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None) == "ghp_saved"


def test_a_supplied_token_still_wins_over_the_saved_one(db, acme):
    _save(db)
    set_session_user(db, acme["admin"].id)
    assert resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token="ghp_typed") == "ghp_typed"


def test_a_non_admin_never_gets_the_saved_token(db, acme):
    _save(db)
    set_session_user(db, acme["member"].id)
    with pytest.raises(NoGitHubTokenAvailable):
        resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None)


def test_no_session_user_means_no_saved_token(db, acme):
    """Background sweeps resolve tokens with no acting user."""
    _save(db)
    db.execute(text("RESET app.user_id"))
    with pytest.raises(NoGitHubTokenAvailable):
        resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None)


def test_saved_token_is_refused_where_a_client_token_is_refused(db, acme):
    _save(db)
    set_session_user(db, acme["admin"].id)
    with pytest.raises(NoGitHubTokenAvailable):
        resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None, allow_client_token=False)


def test_saved_token_for_another_org_is_not_used(db, acme):
    _save(db, org="other")
    set_session_user(db, acme["admin"].id)
    with pytest.raises(NoGitHubTokenAvailable):
        resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None)


def test_undecryptable_saved_token_is_treated_as_absent(db, acme):
    db.add(SavedToken(org="acme", label="t", encrypted_token="not-a-valid-ciphertext"))
    db.commit()
    set_session_user(db, acme["admin"].id)
    with pytest.raises(NoGitHubTokenAvailable):
        resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None)


def test_owner_and_personal_paths_use_it_too(db, acme):
    _save(db)
    set_session_user(db, acme["admin"].id)
    assert resolve_owner_token(db, user_id=acme["admin"].id, owner="acme", client_token=None) == "ghp_saved"
    assert resolve_personal_token(db, owner_user_id=acme["admin"].id, account_login="acme", client_token=None) == "ghp_saved"


def test_resolving_via_fallback_writes_no_audit_row(db, acme):
    _save(db)
    set_session_user(db, acme["admin"].id)
    before = db.query(AuditLog).filter(AuditLog.action == "token.resolve").count()
    resolve_org_token(db, org_id=acme["org"].id, account_login="acme", client_token=None)
    assert db.query(AuditLog).filter(AuditLog.action == "token.resolve").count() == before
