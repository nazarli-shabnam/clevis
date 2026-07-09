"""Tests for src.services.org_provisioning's GitHub-admin auto-grant logic."""

from unittest.mock import patch

from src.core.db import User
from src.repositories import org_membership_repo, org_repo
from src.services import github_oauth, org_provisioning


def _make_user(db, email: str) -> User:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_first_connector_becomes_admin(db):
    user = _make_user(db, "alice@example.com")

    with patch.object(
        github_oauth,
        "list_user_org_memberships",
        return_value=[github_oauth.GitHubOrgMembership(github_org_id=1, login="acme", role="admin")],
    ):
        org_provisioning.sync_org_admin_memberships(db, user, "fake-token")

    org = org_repo.get_by_login(db, "acme")
    assert org is not None
    membership = org_membership_repo.get(db, org_id=org.id, user_id=user.id)
    assert membership is not None
    assert membership.role == "admin"


def test_second_verified_admin_auto_joins_existing_org(db):
    org = org_repo.get_or_create(db, github_login="acme")
    existing_admin = _make_user(db, "alice@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=existing_admin.id, role="admin")

    carol = _make_user(db, "carol@example.com")
    with patch.object(
        github_oauth,
        "list_user_org_memberships",
        return_value=[github_oauth.GitHubOrgMembership(github_org_id=1, login="acme", role="admin")],
    ):
        org_provisioning.sync_org_admin_memberships(db, carol, "fake-token")

    membership = org_membership_repo.get(db, org_id=org.id, user_id=carol.id)
    assert membership is not None
    assert membership.role == "admin"


def test_non_admin_github_member_not_auto_added(db):
    dave = _make_user(db, "dave@example.com")

    with patch.object(
        github_oauth,
        "list_user_org_memberships",
        return_value=[github_oauth.GitHubOrgMembership(github_org_id=1, login="acme", role="member")],
    ):
        org_provisioning.sync_org_admin_memberships(db, dave, "fake-token")

    org = org_repo.get_by_login(db, "acme")
    assert org is None  # never created — no admin ever connected it


def test_github_api_failure_is_swallowed(db):
    import httpx

    erin = _make_user(db, "erin@example.com")
    with patch.object(github_oauth, "list_user_org_memberships", side_effect=httpx.ConnectError("boom")):
        org_provisioning.sync_org_admin_memberships(db, erin, "fake-token")  # must not raise
