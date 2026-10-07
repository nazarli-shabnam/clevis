"""Tests for GET/PUT /orgs/{org}/scheduled-scans and GET /orgs/{org}/analytics/changes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import ScanResult, User, get_db
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers.scheduled_scans import router

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def world(db):
    users = {}
    for name in ("admin", "member", "outsider"):
        u = User(email=f"{name}@e.com", name=None, password_hash=None, is_workspace_admin=False)
        db.add(u)
        users[name] = u
    db.commit()
    for u in users.values():
        db.refresh(u)
    acme = org_repo.get_or_create(db, github_login="acme")
    globex = org_repo.get_or_create(db, github_login="globex")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["admin"].id, role="admin")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["member"].id, role="member")
    org_membership_repo.get_or_create(db, org_id=globex.id, user_id=users["outsider"].id, role="admin")
    return {"users": users, "acme": acme, "globex": globex}


def _client(db, user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: UserOut(id=user.id, email=user.email, name=None, is_workspace_admin=False)
    app.dependency_overrides[get_db] = lambda: db
    # Overriding require_auth skips its SET app.user_id side effect, which RLS (membership lookups) depends on.
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _scan(db, org, checks, score, age_days):
    scan_results_repo.insert(db, owner=org.github_login, score=score, total_checks=len(checks),
                             failed_checks=0, checks=checks, tenant_id=org.tenant_id)
    row = db.query(ScanResult).order_by(ScanResult.id.desc()).first()
    row.created_at = NOW - timedelta(days=age_days)
    db.commit()


def _check(cid, status, repos=None, title=None):
    c = {"id": cid, "title": title or cid.upper(), "severity": "high", "status": status, "scored": True}
    c["value"] = {"repos": repos} if repos else {}
    return c


# --- settings ----------------------------------------------------------------------------------------


def test_settings_default_follows_the_instance(db, world):
    with patch("src.services.scan_schedule.get_config", return_value="weekly"):
        body = _client(db, world["users"]["admin"]).get("/orgs/acme/scheduled-scans").json()
    assert body == {"enabled": None, "effective": True, "cadence": "weekly", "instance_cadence": "weekly"}


def test_admin_can_override_clear_it_and_each_change_is_audited(db, world):
    admin = _client(db, world["users"]["admin"])
    with patch("src.services.scan_schedule.get_config", return_value="off"):
        on = admin.put("/orgs/acme/scheduled-scans", json={"enabled": True}).json()
        assert on == {"enabled": True, "effective": True, "cadence": "weekly", "instance_cadence": "off"}
        same = admin.put("/orgs/acme/scheduled-scans", json={"enabled": True}).json()  # no change: no new audit row
        cleared = admin.put("/orgs/acme/scheduled-scans", json={"enabled": None}).json()
    assert same["enabled"] is True
    assert cleared == {"enabled": None, "effective": False, "cadence": None, "instance_cadence": "off"}
    from src.repositories import audit_repo

    rows = [r for r in audit_repo.list_for_tenant(db, world["acme"].tenant_id) if r.action == "scheduled_scans.updated"]
    assert len(rows) == 2


def test_only_org_admins_can_read_or_change_the_setting(db, world):
    for who in ("member", "outsider"):
        c = _client(db, world["users"][who])
        assert c.get("/orgs/acme/scheduled-scans").status_code == 403
        assert c.put("/orgs/acme/scheduled-scans", json={"enabled": True}).status_code == 403


# --- changes -----------------------------------------------------------------------------------------


def test_changes_with_no_scans_or_one_scan_has_no_previous(db, world):
    member = _client(db, world["users"]["member"])
    assert member.get("/orgs/acme/analytics/changes").json()["has_previous"] is False
    _scan(db, world["acme"], [_check("a", "pass")], 100, 1)
    body = member.get("/orgs/acme/analytics/changes").json()
    assert body["has_previous"] is False and body["score"] == 100


def test_changes_classifies_checks_and_lists_affected_repos(db, world):
    acme = world["acme"]
    _scan(db, acme, [_check("a", "pass"), _check("b", "fail", ["x"]), _check("c", "fail", ["y"]), _check("d", "pass")], 50, 2)
    _scan(db, acme, [_check("a", "fail", ["api", "web"]), _check("b", "pass"), _check("c", "fail", ["y", "z"]), _check("d", "pass")], 40, 1)
    body = _client(db, world["users"]["member"]).get("/orgs/acme/analytics/changes").json()
    assert body["has_previous"] and body["comparable"] is True
    assert (body["previous_score"], body["score"]) == (50, 40)
    got = {c["id"]: (c["change"], c["repos"]) for c in body["changes"]}
    assert got == {"a": ("newly_failing", ["api", "web"]), "b": ("newly_passing", []), "c": ("still_failing", ["y", "z"])}
    assert [c["change"] for c in body["changes"]] == ["newly_failing", "still_failing", "newly_passing"]  # worst first


def test_changes_flags_scans_that_are_not_comparable(db, world):
    acme = world["acme"]
    _scan(db, acme, [_check("a", "pass")], 100, 2)
    _scan(db, acme, [_check("a", "error")], 0, 1)  # an errored check is not a real regression
    body = _client(db, world["users"]["member"]).get("/orgs/acme/analytics/changes").json()
    assert body["comparable"] is False


def test_changes_are_tenant_scoped_and_need_membership(db, world):
    _scan(db, world["globex"], [_check("a", "pass")], 90, 2)
    _scan(db, world["globex"], [_check("a", "fail")], 10, 1)
    assert _client(db, world["users"]["member"]).get("/orgs/acme/analytics/changes").json()["has_previous"] is False
    assert _client(db, world["users"]["member"]).get("/orgs/globex/analytics/changes").status_code == 403


def test_an_earlier_transient_error_that_now_passes_is_not_reported_as_fixed(db, world):
    acme = world["acme"]
    _scan(db, acme, [_check("a", "error"), _check("b", "fail")], 50, 2)
    _scan(db, acme, [_check("a", "pass"), _check("b", "pass")], 100, 1)
    body = _client(db, world["users"]["member"]).get("/orgs/acme/analytics/changes").json()
    assert {c["id"]: c["change"] for c in body["changes"]} == {"b": "newly_passing"}
