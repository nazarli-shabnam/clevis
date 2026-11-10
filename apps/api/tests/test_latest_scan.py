"""GET /orgs/{org}/analytics/latest-scan: the newest stored scan with its checks, scoped like the org's history."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import ScanResult, User, get_db, set_session_user
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers.analytics import router

CHECK = {"id": "repository_license_present", "title": "License present", "severity": "low", "status": "fail",
         "remediation": "Add a license", "value": None}


def _user(db, email: str) -> UserOut:
    row = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
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
def member(db, org):
    user = _user(db, "member@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member")
    return user


def _seed(db, org, score: int, checks: list[dict]) -> None:
    scan_results_repo.insert(
        db, owner="acme", score=score, total_checks=len(checks), failed_checks=1, checks=checks, tenant_id=org.tenant_id
    )


def test_returns_the_newest_scan_with_every_check(db, org, member):
    _seed(db, org, 50, [CHECK])
    _seed(db, org, 83, [CHECK, {**CHECK, "id": "other", "status": "pass"}])

    resp = _client(db, member).get("/orgs/acme/analytics/latest-scan")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["owner"], body["score"], body["total_checks"]) == ("acme", 83, 2)
    assert [c["id"] for c in body["checks"]] == ["repository_license_present", "other"]
    assert body["scanned_at"]


def test_404_when_the_org_has_never_been_scanned(db, org, member):
    assert _client(db, member).get("/orgs/acme/analytics/latest-scan").status_code == 404


def test_requires_org_membership(db, org):
    _seed(db, org, 83, [CHECK])
    stranger = _user(db, "stranger@example.com")
    assert _client(db, stranger).get("/orgs/acme/analytics/latest-scan").status_code in (403, 404)


def test_ignores_a_strangers_scan_of_the_same_login(db, org, member):
    """Another tenant's scan of the same owner string must not become the org's latest result."""
    _seed(db, org, 83, [CHECK])
    stranger = _user(db, "stranger@example.com")
    set_session_user(db, stranger.id)
    from src.repositories import tenant_repo

    personal = tenant_repo.ensure_personal_tenant(db, stranger.id)
    scan_results_repo.insert(
        db, owner="acme", score=5, total_checks=1, failed_checks=1, checks=[], tenant_id=personal.id,
        scanned_by_user_id=stranger.id,
    )

    assert _client(db, member).get("/orgs/acme/analytics/latest-scan").json()["score"] == 83


def test_an_unparseable_stored_checks_blob_gives_an_empty_list_not_a_500(db, org, member):
    _seed(db, org, 83, [CHECK])
    row = db.query(ScanResult).filter(ScanResult.owner == "acme").one()
    row.checks_json = "not json"
    db.commit()

    resp = _client(db, member).get("/orgs/acme/analytics/latest-scan")

    assert resp.status_code == 200 and resp.json()["checks"] == []
    assert json.loads(json.dumps(resp.json()))["score"] == 83
