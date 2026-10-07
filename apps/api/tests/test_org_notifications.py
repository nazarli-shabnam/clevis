"""Tests for GET/POST /orgs/{org}/notifications (derived feed + per-user read marker)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import Job, ScanResult, User, get_db
from src.repositories import installation_repo, job_repo, org_membership_repo, org_repo, scan_results_repo
from src.routers.org_notifications import router

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def world(db):
    users = {}
    for name in ("admin", "member", "other_admin"):
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
    org_membership_repo.get_or_create(db, org_id=globex.id, user_id=users["other_admin"].id, role="admin")
    return {"users": users, "acme": acme, "globex": globex}


def _client(db, user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _alert(db, tenant_id, *, number, severity="critical", state="open", kind="dependabot", when=None, repo="acme/api"):
    db.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))
    db.execute(
        text(
            "INSERT INTO security_alerts (tenant_id, repo, kind, number, state, severity, details, created_at, updated_at) "
            "VALUES (:t, :repo, :kind, :n, :state, :sev, :d, :at, :at)"
        ),
        {"t": tenant_id, "repo": repo, "kind": kind, "n": number, "state": state, "sev": severity,
         "d": json.dumps({"summary": "RCE in lib"}), "at": when or NOW},
    )
    db.commit()


def _scan(db, tenant_id, score, *, age_days=0, checks=None):
    checks = checks if checks is not None else [{"id": "a", "status": "pass", "scored": True}]
    scan_results_repo.insert(db, owner="acme", score=score, total_checks=10, failed_checks=10 - score // 10,
                             checks=checks, tenant_id=tenant_id)
    row = db.query(ScanResult).order_by(ScanResult.id.desc()).first()
    row.created_at = NOW - timedelta(days=age_days)
    db.commit()


def _failed_job(db, tenant_id, *, age_days=0, result="boom"):
    jid = job_repo.enqueue(db, "github.backfill_repo_events", {"x": 1}, tenant_id=tenant_id)
    db.query(Job).filter(Job.id == jid).update({"status": "failed", "result": result, "updated_at": NOW - timedelta(days=age_days)})
    db.commit()
    return jid


def _kinds(resp):
    return sorted(i["kind"] for i in resp.json()["items"])


def test_members_see_alerts_and_score_drops_but_not_admin_only_items(db, world):
    t = world["acme"].tenant_id
    _alert(db, t, number=1)
    _scan(db, t, 90, age_days=2)
    _scan(db, t, 70, age_days=1)
    _failed_job(db, t)

    member = _client(db, world["users"]["member"]).get("/orgs/acme/notifications")
    admin = _client(db, world["users"]["admin"]).get("/orgs/acme/notifications")

    assert _kinds(member) == ["critical_alert", "score_drop"]
    assert _kinds(admin) == ["critical_alert", "job_failed", "score_drop"]
    assert member.json()["unread_count"] == 2
    drop = next(i for i in member.json()["items"] if i["kind"] == "score_drop")
    assert drop["title"] == "Security score dropped from 90 to 70"


def test_only_new_open_critical_dependabot_alerts_count(db, world):
    t = world["acme"].tenant_id
    _alert(db, t, number=1, severity="high")
    _alert(db, t, number=2, state="fixed")
    _alert(db, t, number=3, kind="code_scanning")
    _alert(db, t, number=4, when=NOW - timedelta(days=30))
    _alert(db, t, number=5)
    resp = _client(db, world["users"]["member"]).get("/orgs/acme/notifications")
    assert [i["title"] for i in resp.json()["items"]] == ["New critical Dependabot alert in acme/api"]


def test_score_changes_that_are_not_real_drops_are_ignored(db, world):
    t = world["acme"].tenant_id
    _scan(db, t, 60, age_days=4)
    _scan(db, t, 80, age_days=3)  # improvement
    _scan(db, t, 50, age_days=2, checks=[{"id": "a", "status": "error"}])  # errored check: not comparable
    _scan(db, t, 40, age_days=1, checks=[{"id": "b", "status": "fail", "scored": True}])  # different scored set
    resp = _client(db, world["users"]["member"]).get("/orgs/acme/notifications")
    assert _kinds(resp) == []


def test_failed_jobs_are_tenant_scoped_recent_and_hide_nothing_sensitive(db, world):
    t = world["acme"].tenant_id
    mine = _failed_job(db, t)
    _failed_job(db, world["globex"].tenant_id)
    _failed_job(db, t, age_days=30)
    items = _client(db, world["users"]["admin"]).get("/orgs/acme/notifications").json()["items"]
    assert [i["id"] for i in items] == [f"job_failed:{mine}"]
    assert set(items[0]) == {"id", "kind", "at", "title", "detail", "href", "read"}


def test_permission_drift_shows_for_admins_when_automations_are_blocked(db, world):
    inst = installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=7, org_id=world["acme"].id
    )
    inst.granted_permissions = {"metadata": "read"}
    inst.permissions_synced_at = NOW
    db.commit()
    admin = _client(db, world["users"]["admin"]).get("/orgs/acme/notifications")
    member = _client(db, world["users"]["member"]).get("/orgs/acme/notifications")
    assert _kinds(admin) == ["permission_drift"]
    assert _kinds(member) == []


def test_mark_read_is_per_user_and_per_org(db, world):
    t = world["acme"].tenant_id
    _alert(db, t, number=1, when=NOW - timedelta(minutes=5))
    admin, member = _client(db, world["users"]["admin"]), _client(db, world["users"]["member"])

    assert admin.post("/orgs/acme/notifications/read").status_code == 204

    after = admin.get("/orgs/acme/notifications").json()
    assert after["unread_count"] == 0 and after["items"][0]["read"] is True and after["last_read_at"] is not None
    assert member.get("/orgs/acme/notifications").json()["unread_count"] == 1  # another user's marker is untouched

    _alert(db, t, number=2, when=datetime.now(timezone.utc) + timedelta(minutes=5))  # newer than the marker
    again = admin.get("/orgs/acme/notifications").json()
    assert again["unread_count"] == 1


def test_mark_read_up_to_leaves_later_items_unread_and_is_clamped_to_now(db, world):
    t = world["acme"].tenant_id
    seen = NOW - timedelta(minutes=10)
    _alert(db, t, number=1, when=seen)
    _alert(db, t, number=2, when=NOW - timedelta(minutes=1))  # arrived after the feed was loaded
    admin = _client(db, world["users"]["admin"])

    assert admin.post("/orgs/acme/notifications/read", json={"up_to": seen.isoformat()}).status_code == 204
    assert admin.get("/orgs/acme/notifications").json()["unread_count"] == 1

    far_future = (NOW + timedelta(days=365)).isoformat()
    assert admin.post("/orgs/acme/notifications/read", json={"up_to": far_future}).status_code == 204
    marker = admin.get("/orgs/acme/notifications").json()["last_read_at"]
    assert datetime.fromisoformat(marker) <= datetime.now(timezone.utc)


def test_unread_count_is_not_cut_off_by_the_item_limit(db, world):
    t = world["acme"].tenant_id
    for n in range(40):
        _alert(db, t, number=n)
    for n in range(40):
        _failed_job(db, t)
    feed = _client(db, world["users"]["admin"]).get("/orgs/acme/notifications").json()
    assert len(feed["items"]) == 50
    assert feed["unread_count"] == 80


def test_unread_count_includes_rows_past_the_per_source_limit(db, world):
    t = world["acme"].tenant_id
    for n in range(45):
        _alert(db, t, number=n)
    for _ in range(45):
        _failed_job(db, t)
    admin = _client(db, world["users"]["admin"])
    assert admin.get("/orgs/acme/notifications").json()["unread_count"] == 90
    # A member cannot see jobs, so they are not counted for them either.
    assert _client(db, world["users"]["member"]).get("/orgs/acme/notifications").json()["unread_count"] == 45
    admin.post("/orgs/acme/notifications/read")
    assert admin.get("/orgs/acme/notifications").json()["unread_count"] == 0


def test_the_read_marker_only_moves_forward(db, world):
    from src.repositories import notification_read_repo as repo

    t, uid = world["acme"].tenant_id, world["users"]["admin"].id
    repo.mark_read(db, uid, t, NOW)
    repo.mark_read(db, uid, t, NOW - timedelta(hours=1))
    assert repo.get_last_read(db, uid, t) == NOW


def test_access_is_limited_to_org_members(db, world):
    assert _client(db, world["users"]["other_admin"]).get("/orgs/acme/notifications").status_code == 403
    assert _client(db, world["users"]["other_admin"]).post("/orgs/acme/notifications/read").status_code == 403
    assert _client(db, world["users"]["member"]).get("/orgs/nosuch/notifications").status_code == 404


def test_requires_auth(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    assert TestClient(app).get("/orgs/acme/notifications").status_code == 401
