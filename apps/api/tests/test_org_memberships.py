"""Org admins manage invite-sourced Clevis members; GitHub-sourced ones are read-only; the last admin stays."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, Membership, User, get_db, set_session_tenant, set_session_user
from src.repositories import org_membership_repo, org_repo
from src.routers.org_memberships import router


def _user(db, email: str, github_login: str | None = None) -> UserOut:
    row = User(email=email, name=None, password_hash=None, is_workspace_admin=False, github_login=github_login)
    db.add(row)
    db.commit()
    db.refresh(row)
    return UserOut(id=row.id, email=row.email, name=row.name, is_workspace_admin=False)


def _client(db, user: UserOut) -> TestClient:
    def _authenticate() -> UserOut:
        set_session_user(db, user.id)
        return user

    app = FastAPI()
    app.dependency_overrides[require_auth] = _authenticate
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def org(db):
    return org_repo.get_or_create(db, github_login="acme")


@pytest.fixture()
def admin(db, org):
    user = _user(db, "admin@example.com", github_login="the-admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin", source="github")
    return user


@pytest.fixture()
def contractor(db, org):
    user = _user(db, "contractor@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member", source="invite")
    return user


def _role(db, org, user):
    # memberships is tenant-isolated: read it in that org's context, whichever org the request just used
    set_session_tenant(db, org.tenant_id)
    m = db.query(Membership).filter(Membership.tenant_id == org.tenant_id, Membership.user_id == user.id).first()
    return m.role if m else None


def _audit(db, action: str):
    return db.query(AuditLog).filter(AuditLog.action == action).all()


def test_lists_members_with_their_source_and_whether_they_are_editable(db, org, admin, contractor):
    rows = _client(db, admin).get("/orgs/acme/members").json()

    by_email = {r["email"]: r for r in rows}
    assert by_email["admin@example.com"]["source"] == "github" and by_email["admin@example.com"]["editable"] is False
    assert by_email["admin@example.com"]["github_login"] == "the-admin"
    assert by_email["contractor@example.com"]["source"] == "invite" and by_email["contractor@example.com"]["editable"] is True


def test_listing_and_changes_are_admin_only(db, org, admin, contractor):
    http = _client(db, contractor)  # a plain member
    assert http.get("/orgs/acme/members").status_code == 403
    assert http.put(f"/orgs/acme/members/{contractor.id}", json={"role": "admin"}).status_code == 403
    assert http.delete(f"/orgs/acme/members/{contractor.id}").status_code == 403
    assert _role(db, org, contractor) == "member"


def test_an_admin_changes_an_invite_members_role_and_it_is_audited(db, org, admin, contractor):
    resp = _client(db, admin).put(f"/orgs/acme/members/{contractor.id}", json={"role": "admin"})

    assert resp.status_code == 200 and resp.json()["role"] == "admin"
    assert _role(db, org, contractor) == "admin"
    (entry,) = _audit(db, "membership.update")
    assert entry.actor == "admin@example.com" and entry.target == "acme"
    assert '"previous_role": "member"' in entry.payload and '"role": "admin"' in entry.payload


def test_an_admin_removes_an_invite_member_and_it_is_audited(db, org, admin, contractor):
    resp = _client(db, admin).delete(f"/orgs/acme/members/{contractor.id}")

    assert resp.status_code == 204
    assert _role(db, org, contractor) is None
    assert len(_audit(db, "membership.remove")) == 1


def test_github_sourced_members_cannot_be_changed_or_removed(db, org, admin):
    other = _user(db, "gh@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=other.id, role="member", source="github")
    http = _client(db, admin)

    assert http.put(f"/orgs/acme/members/{other.id}", json={"role": "admin"}).status_code == 409
    assert http.delete(f"/orgs/acme/members/{other.id}").status_code == 409
    assert _role(db, org, other) == "member"
    assert _audit(db, "membership.update") == [] and _audit(db, "membership.remove") == []


def test_a_member_can_leave_an_org_they_joined_by_invitation_and_it_is_audited(db, org, admin, contractor):
    assert _client(db, contractor).delete("/orgs/acme/members/me").status_code == 204
    assert _role(db, org, contractor) is None
    row = _audit(db, "membership.left")[0]
    assert row.actor == "contractor@example.com" and row.target == "acme"
    assert '"source": "invite"' in row.payload
    # They are out: a second attempt finds no membership at all.
    assert _client(db, contractor).delete("/orgs/acme/members/me").status_code == 403


def test_a_github_sourced_membership_cannot_be_left_here(db, org, admin):
    other_admin = _user(db, "second@example.com", github_login="second")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=other_admin.id, role="admin", source="github")
    resp = _client(db, other_admin).delete("/orgs/acme/members/me")
    assert resp.status_code == 409 and "GitHub" in resp.json()["detail"]
    assert _role(db, org, other_admin) == "admin"


def test_the_last_admin_cannot_leave(db, org):
    only = _user(db, "only@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=only.id, role="admin", source="invite")
    resp = _client(db, only).delete("/orgs/acme/members/me")
    assert resp.status_code == 409 and "at least one admin" in resp.json()["detail"]
    assert _role(db, org, only) == "admin"


def test_leaving_does_not_touch_other_members_and_me_is_not_a_user_id(db, org, admin, contractor):
    assert _client(db, contractor).delete("/orgs/acme/members/me").status_code == 204
    assert _role(db, org, admin) == "admin"
    assert _client(db, admin).delete("/orgs/acme/members/not-a-number").status_code == 422


def test_the_last_admin_cannot_be_removed_or_demoted(db, org, admin):
    only = _user(db, "only-invited-admin@example.com")
    # make the invited user the sole admin: the github admin is demoted out of the way first
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=only.id, role="admin", source="invite")
    org_membership_repo.update_role(db, org.id, admin.id, "member")
    http = _client(db, only)

    assert http.delete(f"/orgs/acme/members/{only.id}").status_code == 409
    assert http.put(f"/orgs/acme/members/{only.id}", json={"role": "member"}).status_code == 409
    assert _role(db, org, only) == "admin"


def test_an_invited_admin_can_step_down_while_another_admin_remains(db, org, admin):
    invited_admin = _user(db, "invited-admin@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=invited_admin.id, role="admin", source="invite")

    assert _client(db, invited_admin).put(f"/orgs/acme/members/{invited_admin.id}", json={"role": "member"}).status_code == 200
    assert _role(db, org, invited_admin) == "member"


def test_unknown_user_is_404_and_an_invalid_role_is_422(db, org, admin, contractor):
    http = _client(db, admin)
    assert http.delete("/orgs/acme/members/999999").status_code == 404
    assert http.put(f"/orgs/acme/members/{contractor.id}", json={"role": "owner"}).status_code == 422


def test_a_membership_in_another_org_is_not_reachable_through_this_one(db, org, admin):
    other_org = org_repo.get_or_create(db, github_login="other-org")
    stranger = _user(db, "stranger@example.com")
    org_membership_repo.get_or_create(db, org_id=other_org.id, user_id=stranger.id, role="member", source="invite")

    assert _client(db, admin).delete(f"/orgs/acme/members/{stranger.id}").status_code == 404
    assert _role(db, other_org, stranger) == "member"
