"""Saving a legacy PAT best-effort connects the org to Clevis.

Only a GitHub admin/owner is auto-connected; plain members still use the invite flow.
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth, require_workspace_admin
from src.core.db import AuditLog, User, get_db
from src.core.rbac import require_org_role
from src.repositories import org_repo, tenant_repo
from src.routers.tokens import router as tokens_router
from src.services.github_oauth import GitHubOrgMembership

_PATCH_TARGET = "src.services.org_provisioning.github_oauth.list_user_org_memberships"


def _admin_client(db, user: UserOut) -> TestClient:
    app = FastAPI()
    app.include_router(tokens_router, prefix="/tokens")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_workspace_admin] = lambda: user
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


@pytest.fixture()
def admin(db) -> UserOut:
    u = User(email="wsadmin@example.com", name=None, password_hash=None, is_workspace_admin=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return UserOut(id=u.id, email=u.email, name=u.name, is_workspace_admin=True)


def test_pat_for_admin_org_creates_org_and_membership(db, admin):
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=42, login="Acme", role="admin")]

    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_valid", "label": "acme"})
    assert resp.status_code == 200

    org = org_repo.get_by_login_ci(db, "acme")
    assert org is not None and org.github_org_id == 42
    # RBAC reads the tenant-scoped memberships table.
    membership = tenant_repo.get_membership(db, org.tenant_id, admin.id)
    assert membership is not None and membership.role == "admin"

    logs = db.query(AuditLog).filter(AuditLog.action == "token.org_autolinked").all()
    assert len(logs) == 1 and logs[0].target == "Acme"


def test_require_org_role_passes_after_autolink(db, admin):
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=7, login="acme", role="admin")]
    with patch(_PATCH_TARGET, return_value=memberships):
        client.put("/tokens/acme", json={"token": "ghp_valid"})

    ctx = require_org_role("member")(org_login="acme", db=db, user=admin)
    assert ctx.org.github_login == "acme"


def test_github_error_still_saves_token(db, admin):
    import httpx

    client = _admin_client(db, admin)
    with patch(
        _PATCH_TARGET,
        side_effect=httpx.HTTPStatusError("403", request=None, response=None),
    ):
        resp = client.put("/tokens/acme", json={"token": "ghp_noscope"})
    assert resp.status_code == 200
    assert org_repo.get_by_login_ci(db, "acme") is None


def test_pat_without_matching_membership_saves_token_only(db, admin):
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=1, login="other-org", role="admin")]
    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_valid"})
    assert resp.status_code == 200
    assert org_repo.get_by_login_ci(db, "acme") is None


def test_pat_for_plain_member_saves_token_but_does_not_connect_org(db, admin):
    """A GitHub member (not admin) pasting a PAT gets the token saved but no Org / membership."""
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=99, login="Acme", role="member")]
    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_valid"})
    assert resp.status_code == 200
    assert org_repo.get_by_login_ci(db, "acme") is None
    assert db.query(AuditLog).filter(AuditLog.action == "token.org_autolinked").count() == 0


def test_existing_member_row_is_promoted_when_github_says_admin(db, admin):
    """An existing 'member' row is promoted when GitHub now reports the caller as admin."""
    org = org_repo.get_or_create(db, github_login="Acme", github_org_id=55)
    from src.repositories import org_membership_repo

    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="member")
    db.commit()

    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=55, login="Acme", role="admin")]
    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_valid"})
    assert resp.status_code == 200

    membership = tenant_repo.get_membership(db, org.tenant_id, admin.id)
    assert membership is not None and membership.role == "admin"


def test_token_save_and_delete_are_audited(db, admin):
    client = _admin_client(db, admin)
    with patch(_PATCH_TARGET, return_value=[]):
        assert client.put("/tokens/acme", json={"token": "ghp_valid", "label": "ci"}).status_code == 200
    assert client.delete("/tokens/acme").status_code == 204

    actions = [r.action for r in db.query(AuditLog).filter(AuditLog.target == "acme").order_by(AuditLog.id)]
    assert "token.save" in actions and "token.delete" in actions
