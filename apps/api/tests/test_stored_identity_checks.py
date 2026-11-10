"""The identity checks are fed from Clevis's own tables, only for the org they belong to (#815)."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import OrgMember, RepoCollaborator, User, get_db, set_session_tenant
from src.repositories import org_membership_repo, org_repo
from src.routers.analytics import router
from src.services import analytics_service
from src.services.stored_identity import load_stored_identity

OVERVIEW = {"owner": "acme", "score": 100, "total_checks": 0, "failed_checks": 0, "repo_count": 0, "checks": []}


def _user(db, email: str) -> UserOut:
    row = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return UserOut(id=row.id, email=row.email, name=row.name, is_workspace_admin=False)


_NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _seed(db, org):
    set_session_tenant(db, org.tenant_id)  # org_members / repo_collaborators are tenant-isolated
    db.add(OrgMember(tenant_id=org.tenant_id, login="alice", avatar_url="", role="member", two_factor_enabled=False, added_at=_NOW))
    db.add(OrgMember(tenant_id=org.tenant_id, login="bob", avatar_url="", role="admin", two_factor_enabled=None, added_at=_NOW))
    db.add(RepoCollaborator(
        tenant_id=org.tenant_id, repo="acme/api", login="contractor", permission="admin", source="direct",
        is_outside_collaborator=True, granted_at=_NOW,
    ))
    db.commit()


def _client(db, user):
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def org(db):
    return org_repo.get_or_create(db, github_login="acme")


def test_loader_returns_this_orgs_members_and_collaborators_only(db, org):
    other = org_repo.get_or_create(db, github_login="other-org")
    _seed(db, org)
    set_session_tenant(db, other.tenant_id)  # org_members is tenant-isolated: write the other org's row in its context
    db.add(OrgMember(tenant_id=other.tenant_id, login="intruder", avatar_url="", role="member", two_factor_enabled=False, added_at=_NOW))
    db.commit()

    stored = load_stored_identity(db, org.tenant_id)

    assert sorted((m["login"], m["two_factor_enabled"]) for m in stored["members"]) == [("alice", False), ("bob", None)]
    assert stored["collaborators"] == [
        {"repo": "acme/api", "login": "contractor", "permission": "admin", "is_outside_collaborator": True}
    ]


def test_org_overview_passes_the_orgs_stored_data_to_the_scan(db, org):
    admin = _user(db, "admin@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    _seed(db, org)

    with patch("src.routers.analytics.get_overview", return_value=OVERVIEW) as overview:
        resp = _client(db, admin).post("/orgs/acme/analytics/overview", json={"owner": "acme", "token": "ghp_test"})

    assert resp.status_code == 200
    stored = overview.call_args.kwargs["stored_identity"]
    assert {m["login"] for m in stored["members"]} == {"alice", "bob"} and len(stored["collaborators"]) == 1


def test_a_members_personal_scan_of_their_org_gets_the_stored_data(db, org):
    admin = _user(db, "admin@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    _seed(db, org)

    with patch("src.routers.analytics.get_account_type", return_value="Organization"), patch(
        "src.routers.analytics.get_overview", return_value=OVERVIEW
    ) as overview:
        _client(db, admin).post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_test"})

    assert len(overview.call_args.kwargs["stored_identity"]["members"]) == 2


def test_a_non_members_scan_of_the_same_login_gets_no_stored_data(db, org):
    """BYO-token scan of someone else's org login must not read that org's members or collaborators."""
    _seed(db, org)
    stranger = _user(db, "stranger@example.com")

    with patch("src.routers.analytics.get_account_type", return_value="Organization"), patch(
        "src.routers.analytics.get_overview", return_value=OVERVIEW
    ) as overview:
        _client(db, stranger).post("/me/analytics/overview", json={"owner": "acme", "token": "ghp_test"})

    assert overview.call_args.kwargs["stored_identity"] is None


def test_get_overview_hands_the_data_to_the_runner_and_keeps_the_checks_unscored_by_default():
    identity_checks = [
        {"id": "organization_members_2fa_enabled", "title": "t", "severity": "medium", "remediation": "r",
         "informational": True, "status": "fail", "value": {"enabled": 0, "total": 1, "unknown": 0}},
    ]
    report = {"checks": identity_checks, "repo_count": 0}
    stored = {"members": [], "collaborators": []}

    # get_overview stamps its input in place, so each scan gets its own copy
    with patch("src.services.analytics_service.run_all_checks", side_effect=lambda **_kw: deepcopy(report)) as runner:
        unscored = analytics_service.get_overview("acme", "t", score_hygiene=False, stored_identity=stored)
        scored = analytics_service.get_overview("acme", "t", score_hygiene=True, stored_identity=stored)

    assert runner.call_args.kwargs["stored_identity"] is stored
    # informational: shown but not counted until the org opts in
    assert unscored["checks"][0]["scored"] is False and unscored["failed_checks"] == 0
    assert scored["checks"][0]["scored"] is True and scored["failed_checks"] == 1
