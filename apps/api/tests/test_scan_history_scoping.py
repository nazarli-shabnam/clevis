"""Scan history is scoped to the org that owns it, not to whoever typed the same owner login.

POST /me/analytics/overview accepts any owner the caller has a token for, so a row's ``owner`` string
alone proves nothing about whose history it belongs to (#530).
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import ScanResult, User, get_db, set_session_user
from src.repositories import installation_repo, org_membership_repo, org_repo, scan_results_repo, tenant_repo
from src.routers.analytics import router
from src.services import digest_service

OVERVIEW = {
    "owner": "acme",
    "score": 80,
    "total_checks": 1,
    "failed_checks": 0,
    "repo_count": 4,
    "checks": [],
}


def _user(db, email: str) -> UserOut:
    row = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    return UserOut(id=row.id, email=row.email, name=row.name, is_workspace_admin=False)


def _client(db, user: UserOut) -> TestClient:
    def _authenticate() -> UserOut:
        # What the real require_auth does: self-identify to RLS for this request. Without it, a
        # previously created user's context (e.g. from ensure_personal_tenant) leaks into the request.
        set_session_user(db, user.id)
        return user

    app = FastAPI()
    app.dependency_overrides[require_auth] = _authenticate
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(router)
    return TestClient(app)


def _seed(db, owner: str, score: int, tenant_id: int, user_id: int | None = None) -> None:
    scan_results_repo.insert(
        db, owner=owner, score=score, total_checks=1, failed_checks=0, checks=[],
        tenant_id=tenant_id, scanned_by_user_id=user_id,
    )


def _scan_via_personal_route(http: TestClient, score: int, owner: str = "acme") -> None:
    with (
        patch("src.routers.analytics.get_account_type", return_value="Organization"),
        patch("src.routers.analytics.get_overview", return_value={**OVERVIEW, "owner": owner, "score": score}),
    ):
        resp = http.post("/me/analytics/overview", json={"owner": owner, "token": "ghp_test"})
    assert resp.status_code == 200


@pytest.fixture()
def org(db):
    return org_repo.get_or_create(db, github_login="acme")


@pytest.fixture()
def member(db, org):
    # An admin: scanning with a pasted (BYO) token needs it since #582. Reading org history only needs
    # membership, which is what these tests are about; the plain-member case is covered in test_analytics.
    user = _user(db, "member@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin")
    return user


@pytest.fixture()
def outsider(db):
    return _user(db, "outsider@example.com")


def _scores(rows) -> list[int]:
    return [r["score"] for r in rows]


# ── the exploit: a non-member's scan of an org's login ───────────────────────


def test_a_non_members_scan_of_an_org_login_never_reaches_the_orgs_history_export_or_trend(db, org, member, outsider):
    _scan_via_personal_route(_client(db, outsider), score=5)  # BYO-PAT scan of "acme" by a stranger
    _seed(db, "acme", 90, org.tenant_id, member.id)
    http = _client(db, member)

    assert _scores(http.get("/orgs/acme/analytics/history").json()) == [90]
    assert _scores(http.get("/me/analytics/history?owner=acme").json()) == [90]
    assert [e["score"] for e in http.get("/orgs/acme/analytics/export").json()["entries"]] == [90]
    assert [e["score"] for e in http.get("/me/analytics/export?owner=acme").json()["entries"]] == [90]


def test_a_non_members_scan_is_stored_under_their_personal_tenant_not_the_orgs(db, org, outsider):
    _scan_via_personal_route(_client(db, outsider), score=5)

    row = db.query(ScanResult).filter(ScanResult.owner == "acme").one()
    assert row.tenant_id == tenant_repo.ensure_personal_tenant(db, outsider.id).id
    assert row.tenant_id != org.tenant_id
    assert row.scanned_by_user_id == outsider.id


def test_the_outsider_still_sees_their_own_scan_but_not_the_orgs(db, org, member, outsider):
    _seed(db, "acme", 90, org.tenant_id, member.id)
    http = _client(db, outsider)
    _scan_via_personal_route(http, score=5)

    # Their own BYO-PAT scan only -- the org's 90 is not theirs to read.
    assert _scores(http.get("/me/analytics/history?owner=acme").json()) == [5]


# ── the write path: a member's scan belongs to the org ───────────────────────


def test_a_members_scan_through_the_personal_route_is_stored_under_the_org_tenant(db, org, member):
    _scan_via_personal_route(_client(db, member), score=77)

    row = db.query(ScanResult).filter(ScanResult.owner == "acme").one()
    assert row.tenant_id == org.tenant_id
    assert row.scanned_by_user_id == member.id


def test_a_members_scan_is_stored_under_the_orgs_canonical_login_whatever_casing_they_typed(db):
    org = org_repo.get_or_create(db, github_login="Acme")
    member = _user(db, "casing@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="admin")
    http = _client(db, member)

    _scan_via_personal_route(http, score=61, owner="acme")

    row = db.query(ScanResult).one()
    assert row.owner == "Acme"
    assert row.tenant_id == org.tenant_id
    assert _scores(http.get("/orgs/Acme/analytics/history").json()) == [61]


def test_another_member_sees_a_scan_a_colleague_ran_through_the_personal_route(db, org, member):
    colleague = _user(db, "colleague@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=colleague.id, role="admin")
    _scan_via_personal_route(_client(db, colleague), score=64)

    assert _scores(_client(db, member).get("/orgs/acme/analytics/history").json()) == [64]


# ── scans stored before members' scans moved to the org tenant ───────────────


def test_a_scan_a_member_ran_before_the_change_is_still_in_the_orgs_history(db, org, member):
    # Old behaviour: personal route stored every scan under the scanner's personal tenant.
    personal = tenant_repo.ensure_personal_tenant(db, member.id)
    _seed(db, "acme", 72, personal.id, member.id)

    assert _scores(_client(db, member).get("/orgs/acme/analytics/history").json()) == [72]


def test_a_scan_by_someone_who_is_not_a_member_is_excluded_even_from_a_personal_tenant_row(db, org, member, outsider):
    _seed(db, "acme", 72, tenant_repo.ensure_personal_tenant(db, member.id).id, member.id)
    _seed(db, "acme", 3, tenant_repo.ensure_personal_tenant(db, outsider.id).id, outsider.id)

    assert _scores(_client(db, member).get("/orgs/acme/analytics/history").json()) == [72]


def test_owner_matching_for_the_org_is_case_insensitive(db, org, member):
    _seed(db, "ACME", 55, org.tenant_id, member.id)

    assert _scores(_client(db, member).get("/orgs/acme/analytics/history").json()) == [55]


# ── "own" scope: nobody else's rows for a login that is not an org they belong to ─────────


def test_a_personal_installation_owner_only_sees_their_own_scans(db):
    owner = _user(db, "solo@example.com")
    stranger = _user(db, "stranger@example.com")
    installation_repo.upsert(
        db, account_login="solo", account_type="User", auth_mode="app", installation_id=1, owner_user_id=owner.id
    )
    _seed(db, "solo", 70, tenant_repo.ensure_personal_tenant(db, owner.id).id, owner.id)
    # A stranger scanned the "solo" login with their own token.
    _seed(db, "solo", 2, tenant_repo.ensure_personal_tenant(db, stranger.id).id, stranger.id)

    http = _client(db, owner)
    assert _scores(http.get("/me/analytics/history?owner=solo").json()) == [70]
    assert [e["score"] for e in http.get("/me/analytics/export?owner=solo").json()["entries"]] == [70]


def test_a_former_members_older_scans_are_no_longer_in_the_orgs_history(db, org, member):
    former = _user(db, "former@example.com")
    _seed(db, "acme", 41, tenant_repo.ensure_personal_tenant(db, former.id).id, former.id)

    assert _scores(_client(db, member).get("/orgs/acme/analytics/history").json()) == []


def test_a_previous_scan_stored_under_the_canonical_login_is_found_whatever_casing_is_asked_for(db):
    # The score-drop baseline asks for the owner as the caller typed it; org-tenant rows carry the
    # canonical login, and a casing mismatch would silently skip the alert.
    org = org_repo.get_or_create(db, github_login="Acme")
    _seed(db, "Acme", 66, org.tenant_id)

    rows = scan_results_repo.list_recent(db, "acme", limit=1, tenant_id=org.tenant_id)

    assert _scores(rows) == [66]


def test_a_former_member_can_still_read_the_scans_they_ran_themselves(db):
    org = org_repo.get_or_create(db, github_login="Acme")
    user = _user(db, "leaver@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin")
    http = _client(db, user)
    _scan_via_personal_route(http, score=58, owner="acme")  # stored as "Acme" under the org tenant
    org_membership_repo.delete(db, org_id=org.id, user_id=user.id)

    resp = http.get("/me/analytics/history?owner=acme")

    assert resp.status_code == 200
    assert _scores(resp.json()) == [58]


# ── digest ───────────────────────────────────────────────────────────────────


def test_the_digest_ignores_a_newer_scan_by_a_non_member(db, org, member, outsider):
    _seed(db, "acme", 85, org.tenant_id, member.id)
    _seed(db, "acme", 20, tenant_repo.ensure_personal_tenant(db, outsider.id).id, outsider.id)
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))

    content = digest_service.build_digest(db, tenant_id=org.tenant_id, org_login="acme", period_label="weekly")

    assert content.latest_score == 85
    assert content.previous_score is None
