"""Tests for permission-audit and inactive-members routes (docs/plan.md Phase 18)."""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.collab import router as collab_router

_ADMIN = UserOut(id=1, email="admin@example.com", name=None, is_workspace_admin=False)


@pytest.fixture()
def acme_org(db):
    user = User(id=_ADMIN.id, email=_ADMIN.email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member")
    return org


@pytest.fixture()
def client(db, acme_org):
    app = FastAPI()
    app.include_router(collab_router)
    app.dependency_overrides[require_auth] = lambda: _ADMIN
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_permission_audit_no_token_returns_400(client):
    resp = client.get("/github/orgs/acme/permission-audit")
    assert resp.status_code == 400


def test_permission_audit_flags_outside_collaborator_with_write_access(client):
    def _paginated_side_effect(path, params=None):
        if path == "/orgs/acme/members":
            return [{"login": "alice"}]
        if path == "/orgs/acme/outside_collaborators":
            return [{"login": "bob"}]
        if path == "/orgs/acme/repos":
            return [{"name": "api"}]
        if path == "/repos/acme/api/collaborators":
            return [
                {"login": "alice", "avatar_url": "", "permissions": {"pull": True, "push": True, "admin": True}},
                {"login": "bob", "avatar_url": "", "permissions": {"pull": True, "push": True, "admin": False}},
            ]
        return []

    with patch("src.routers.collab.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        resp = client.get("/github/orgs/acme/permission-audit", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    body = resp.json()
    row = body["repos"][0]["collaborators"]
    alice = next(c for c in row if c["login"] == "alice")
    bob = next(c for c in row if c["login"] == "bob")
    assert alice["permission"] == "admin"
    assert alice["is_outside_collaborator"] is False
    assert bob["permission"] == "write"
    assert bob["is_outside_collaborator"] is True
    assert body["risk_summary"]["outside_with_write_or_admin"] == 1
    assert body["risk_summary"]["members_with_admin"] == 1
    assert body["risk_summary"]["total_outside_collaborators"] == 1


def test_permission_audit_one_bad_repo_does_not_blank_others(client):
    def _paginated_side_effect(path, params=None):
        if path == "/orgs/acme/members":
            return [{"login": "alice"}]
        if path == "/orgs/acme/outside_collaborators":
            return []
        if path == "/orgs/acme/repos":
            return [{"name": "repo-bad"}, {"name": "repo-good"}]
        if path == "/repos/acme/repo-bad/collaborators":
            raise httpx.RequestError("boom")
        if path == "/repos/acme/repo-good/collaborators":
            return [{"login": "alice", "avatar_url": "", "permissions": {"pull": True}}]
        return []

    with patch("src.routers.collab.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        resp = client.get("/github/orgs/acme/permission-audit", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    repos_by_name = {r["repo"]: r for r in resp.json()["repos"]}
    assert repos_by_name["repo-bad"]["collaborators"] == []
    assert len(repos_by_name["repo-good"]["collaborators"]) == 1


def test_permission_audit_skips_collaborator_entries_missing_login(client):
    def _paginated_side_effect(path, params=None):
        if path == "/orgs/acme/members":
            return [{"login": "alice"}]
        if path == "/orgs/acme/outside_collaborators":
            return []
        if path == "/orgs/acme/repos":
            return [{"name": "api"}]
        if path == "/repos/acme/api/collaborators":
            return [
                {"permissions": {"pull": True}},  # malformed: no "login"
                {"login": "alice", "avatar_url": "", "permissions": {"pull": True}},
            ]
        return []

    with patch("src.routers.collab.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        resp = client.get("/github/orgs/acme/permission-audit", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    collaborators = resp.json()["repos"][0]["collaborators"]
    assert len(collaborators) == 1
    assert collaborators[0]["login"] == "alice"


def test_permission_audit_outsider_forbidden(db):
    org_repo.get_or_create(db, github_login="acme")
    app = FastAPI()
    app.include_router(collab_router)
    app.dependency_overrides[require_auth] = lambda: UserOut(id=999, email="outsider@example.com", name=None, is_workspace_admin=False)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get("/github/orgs/acme/permission-audit")
    assert resp.status_code == 403


def test_inactive_members_no_token_returns_400(client):
    resp = client.get("/github/orgs/acme/inactive-members")
    assert resp.status_code == 400


def test_inactive_members_flags_member_with_no_recent_commits(client):
    def _paginated_side_effect(path, params=None):
        if path == "/orgs/acme/members" and params == {"role": "admin"}:
            return []
        if path == "/orgs/acme/members":
            return [{"login": "alice", "avatar_url": ""}]
        if path == "/orgs/acme/repos":
            return [{"name": "api"}]
        return []

    with patch("src.routers.collab.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        mock_client.return_value.request.return_value = []  # no commits found by alice
        resp = client.get("/github/orgs/acme/inactive-members?days=30", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["members"]) == 1
    assert body["members"][0]["login"] == "alice"
    assert body["members"][0]["last_commit_days_ago"] is None
    assert body["sampled_repos"] == ["acme/api"]


def test_inactive_members_excludes_recently_active_member(client):
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _paginated_side_effect(path, params=None):
        if path == "/orgs/acme/members" and params == {"role": "admin"}:
            return []
        if path == "/orgs/acme/members":
            return [{"login": "alice", "avatar_url": ""}]
        if path == "/orgs/acme/repos":
            return [{"name": "api"}]
        return []

    with patch("src.routers.collab.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        mock_client.return_value.request.return_value = [{"commit": {"author": {"date": recent}}}]
        resp = client.get("/github/orgs/acme/inactive-members?days=30", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["members"] == []


def test_inactive_members_does_not_flag_a_member_whose_activity_could_not_be_verified(client):
    """A transient API failure on every sampled repo must not be conflated with a
    genuine 'zero commits found' answer -- an unverifiable member is excluded
    entirely rather than wrongly flagged inactive (a false access-risk signal)."""
    def _paginated_side_effect(path, params=None):
        if path == "/orgs/acme/members" and params == {"role": "admin"}:
            return []
        if path == "/orgs/acme/members":
            return [{"login": "alice", "avatar_url": ""}]
        if path == "/orgs/acme/repos":
            return [{"name": "api"}]
        return []

    with patch("src.routers.collab.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        resp = client.get("/github/orgs/acme/inactive-members?days=30", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["members"] == []
