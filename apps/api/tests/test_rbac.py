"""Tests for src.core.rbac's org-role dependency."""

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import org_membership_repo, org_repo

_OUTSIDER = UserOut(id=99999, email="outsider@e.com", name=None, is_workspace_admin=False)


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def rbac_app(db):
    app = FastAPI()

    @app.get("/orgs/{org_login}/member-only")
    def member_route(ctx=Depends(require_org_role(min_role="member"))):
        return {"org": ctx.org.github_login, "role": ctx.membership.role}

    @app.get("/orgs/{org_login}/admin-only")
    def admin_route(ctx=Depends(require_org_role(min_role="admin"))):
        return {"org": ctx.org.github_login, "role": ctx.membership.role}

    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture()
def acme_org(db):
    admin = _make_user(db, "admin@e.com")
    member = _make_user(db, "member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return org, admin, member


def _client(app, user):
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


def test_member_route_allows_member(rbac_app, acme_org):
    _, _admin, member = acme_org
    resp = _client(rbac_app, member).get("/orgs/acme/member-only")
    assert resp.status_code == 200
    assert resp.json() == {"org": "acme", "role": "member"}


def test_member_route_allows_admin(rbac_app, acme_org):
    _, admin, _member = acme_org
    resp = _client(rbac_app, admin).get("/orgs/acme/member-only")
    assert resp.status_code == 200


def test_member_route_rejects_outsider(rbac_app, acme_org):
    resp = _client(rbac_app, _OUTSIDER).get("/orgs/acme/member-only")
    assert resp.status_code == 403


def test_admin_route_rejects_member(rbac_app, acme_org):
    _, _admin, member = acme_org
    resp = _client(rbac_app, member).get("/orgs/acme/admin-only")
    assert resp.status_code == 403


def test_admin_route_allows_admin(rbac_app, acme_org):
    _, admin, _member = acme_org
    resp = _client(rbac_app, admin).get("/orgs/acme/admin-only")
    assert resp.status_code == 200


def test_route_rejects_unknown_org(rbac_app, acme_org):
    _, admin, _member = acme_org
    resp = _client(rbac_app, admin).get("/orgs/does-not-exist/member-only")
    assert resp.status_code == 404


def test_route_requires_auth(rbac_app, acme_org):
    resp = TestClient(rbac_app).get("/orgs/acme/member-only")
    assert resp.status_code == 401


# ── assert_owner_matches_org ────────────────────────────────────────────────────

def test_assert_owner_matches_org_allows_exact_match(db):
    org = org_repo.get_or_create(db, github_login="acme")
    assert_owner_matches_org("acme", OrgContext(org=org, membership=None))


def test_assert_owner_matches_org_is_case_insensitive(db):
    # GitHub logins are case-insensitive (Acme and acme are the same account) --
    # regression test for issue #224 item 1, matching the .lower() comparison already
    # used in apps/api/src/routers/installations.py for the same class of check.
    org = org_repo.get_or_create(db, github_login="acme")
    assert_owner_matches_org("Acme", OrgContext(org=org, membership=None))
    assert_owner_matches_org("ACME", OrgContext(org=org, membership=None))


def test_assert_owner_matches_org_rejects_a_different_org(db):
    org = org_repo.get_or_create(db, github_login="acme")
    with pytest.raises(HTTPException) as exc_info:
        assert_owner_matches_org("widgets-inc", OrgContext(org=org, membership=None))
    assert exc_info.value.status_code == 403
