"""Saving a legacy PAT best-effort connects the org to Clevis.

Only a GitHub admin/owner is auto-connected; plain members still use the invite flow.
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, text

from src.core.auth import UserOut, require_auth, require_workspace_admin
from src.core.db import AuditLog, SavedToken, User, get_db
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


def test_pat_for_a_login_now_owned_by_a_different_github_org_is_not_filed_under_the_old_tenant(db, admin):
    old = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    old_tenant_id = old.tenant_id
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=2, login="acme", role="admin")]

    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_valid"})

    assert resp.status_code == 200
    assert tenant_repo.get_membership(db, old_tenant_id, admin.id) is None
    assert db.query(SavedToken).filter_by(org="acme").one().tenant_id is None


def test_a_token_saved_before_the_login_changed_hands_is_unlinked_from_the_old_tenant(db, admin):
    old = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    db.add(SavedToken(org="acme", label="old", encrypted_token="x", tenant_id=old.tenant_id))
    db.commit()
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=2, login="acme", role="admin")]

    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_new", "label": "new"})

    assert resp.status_code == 200
    row = db.query(SavedToken).filter_by(org="acme").one()
    assert row.label == "new" and row.tenant_id is None
    assert db.query(AuditLog).filter(AuditLog.action == "token.save").one().tenant_id != old.tenant_id


def test_pat_that_cannot_prove_org_admin_does_not_link_a_login_matched_org(db, admin):
    # A workspace admin who is not an admin of the org, and whose PAT does not list it as admin, must not
    # get the token filed under the tenant that merely shares the login.
    org = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    client = _admin_client(db, admin)
    memberships = [GitHubOrgMembership(github_org_id=9, login="other-org", role="admin")]

    with patch(_PATCH_TARGET, return_value=memberships):
        resp = client.put("/tokens/acme", json={"token": "ghp_valid"})

    assert resp.status_code == 200
    assert db.query(SavedToken).filter_by(org="acme").one().tenant_id is None
    assert tenant_repo.get_membership(db, org.tenant_id, admin.id) is None


def test_a_save_that_loses_the_insert_race_updates_the_winners_row_instead_of_500ing(db, admin):
    # #1115: two PUTs for an org with no row both miss the lookup; the loser's INSERT violates the unique
    # constraint at commit. Simulated by hiding the existing row from the first lookup only.
    from src.routers import tokens as tokens_module

    db.add(SavedToken(org="racy", label="winner", encrypted_token="x"))
    db.commit()
    real = tokens_module._find_saved_token
    calls = {"n": 0}

    def blind_once(db_, org):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db_, org)

    client = _admin_client(db, admin)
    with patch(_PATCH_TARGET, return_value=[]), patch.object(tokens_module, "_find_saved_token", blind_once):
        resp = client.put("/tokens/racy", json={"token": "ghp_loser", "label": "loser"})

    assert resp.status_code == 200
    assert resp.json()["label"] == "loser"
    assert db.query(SavedToken).filter(SavedToken.org == "racy").count() == 1


def test_saving_under_a_different_casing_replaces_the_existing_token_instead_of_adding_a_second(db, admin):
    client = _admin_client(db, admin)
    with patch(_PATCH_TARGET, return_value=[]):
        assert client.put("/tokens/Acme", json={"token": "ghp_one"}).status_code == 200
        assert client.put("/tokens/acme", json={"token": "ghp_two", "label": "second"}).status_code == 200

    rows = db.query(SavedToken).filter(SavedToken.org.in_(["Acme", "acme"])).all()
    assert len(rows) == 1 and rows[0].label == "second"


def test_a_resave_and_a_delete_clear_casing_duplicates_left_from_before(db, admin):
    """Rows saved when lookups were case-sensitive can sit side by side; neither may keep an old credential."""
    db.add_all([
        SavedToken(org="Acme", label="old", encrypted_token="x"),
        SavedToken(org="acme", label="newer", encrypted_token="y"),
    ])
    db.commit()
    client = _admin_client(db, admin)
    with patch(_PATCH_TARGET, return_value=[]):
        assert client.put("/tokens/ACME", json={"token": "ghp_rotated", "label": "rotated"}).status_code == 200
    rows = db.query(SavedToken).filter(func.lower(SavedToken.org) == "acme").all()
    assert len(rows) == 1 and rows[0].label == "rotated"

    db.add(SavedToken(org="aCme", label="dup", encrypted_token="z"))
    db.commit()
    assert client.delete("/tokens/acme").status_code == 204
    assert db.query(SavedToken).filter(func.lower(SavedToken.org) == "acme").count() == 0


def test_the_org_name_must_look_like_a_github_login(db, admin):
    client = _admin_client(db, admin)
    for bad in ("a b", "x" * 40, "-lead", "a.b"):
        assert client.put(f"/tokens/{bad}", json={"token": "ghp_x"}).status_code == 422
        assert client.delete(f"/tokens/{bad}").status_code == 422
    assert client.post("/tokens/resolve", json={"org": "../x"}).status_code == 422
