"""Org target score (#1278): the setting, and the standing `below_target` notification while the latest score is under it."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, User, get_db
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers.analytics import router as analytics_router
from src.routers.org_notifications import router as notifications_router

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def world(db):
    users = {}
    for name in ("admin", "member", "outsider"):
        u = User(email=f"{name}@ts.com", name=None, password_hash=None, is_workspace_admin=False)
        db.add(u)
        users[name] = u
    db.commit()
    for u in users.values():
        db.refresh(u)
    acme = org_repo.get_or_create(db, github_login="acme-ts")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["admin"].id, role="admin")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["member"].id, role="member")
    return {"users": users, "org": acme}


def _client(db, user):
    app = FastAPI()
    app.include_router(analytics_router)
    app.include_router(notifications_router)
    app.dependency_overrides[require_auth] = lambda: UserOut(id=user.id, email=user.email, name=None, is_workspace_admin=False)
    app.dependency_overrides[get_db] = lambda: db
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _scan(db, org, score, age_hours=1):
    scan_results_repo.insert(
        db, owner=org.github_login, score=score, total_checks=6, failed_checks=0 if score is None else 1,
        checks=[{"id": "a", "status": "pass", "scored": True}], tenant_id=org.tenant_id,
    )
    from src.core.db import ScanResult

    row = db.query(ScanResult).order_by(ScanResult.id.desc()).first()
    row.created_at = NOW - timedelta(hours=age_hours)
    db.commit()
    return row.id


def _below(client):
    return [i for i in client.get("/orgs/acme-ts/notifications").json()["items"] if i["kind"] == "below_target"]


def test_admin_sets_and_clears_the_target_and_it_is_audited(db, world):
    admin = _client(db, world["users"]["admin"])
    assert admin.get("/orgs/acme-ts/target-score").json() == {"target": None}

    assert admin.put("/orgs/acme-ts/target-score", json={"target": 85}).json() == {"target": 85}
    db.refresh(world["org"])
    assert world["org"].target_score == 85
    audit = db.query(AuditLog).filter(AuditLog.action == "target_score.updated").one()
    assert json.loads(audit.payload) == {"target": 85, "previous": None}

    admin.put("/orgs/acme-ts/target-score", json={"target": 85})  # unchanged: no new row
    assert db.query(AuditLog).filter(AuditLog.action == "target_score.updated").count() == 1
    assert admin.put("/orgs/acme-ts/target-score", json={"target": None}).json() == {"target": None}
    db.refresh(world["org"])
    assert world["org"].target_score is None


@pytest.mark.parametrize("target", [0, 101, -5, "high"])
def test_a_target_outside_1_to_100_is_rejected(db, world, target):
    assert _client(db, world["users"]["admin"]).put("/orgs/acme-ts/target-score", json={"target": target}).status_code == 422


def test_a_member_reads_but_cannot_change_it_and_an_outsider_cannot_read(db, world):
    world["org"].target_score = 90
    db.commit()
    member = _client(db, world["users"]["member"])
    assert member.get("/orgs/acme-ts/target-score").json() == {"target": 90}
    assert member.put("/orgs/acme-ts/target-score", json={"target": 10}).status_code == 403
    assert _client(db, world["users"]["outsider"]).get("/orgs/acme-ts/target-score").status_code == 403


def test_the_feed_shows_a_standing_item_for_every_member_while_the_latest_score_is_under_the_target(db, world):
    org = world["org"]
    org.target_score = 85
    db.commit()
    _scan(db, org, 90, age_hours=5)
    newest = _scan(db, org, 72, age_hours=1)

    for who in ("admin", "member"):
        items = _below(_client(db, world["users"][who]))
        assert [i["id"] for i in items] == [f"below_target:{newest}"]
        assert items[0]["title"] == "Security score 72 is below the target of 85" and items[0]["detail"] == "13 points to go."
        assert items[0]["href"] == "/security"


def test_no_item_without_a_target_a_scored_scan_or_once_the_target_is_met(db, world):
    org = world["org"]
    admin = _client(db, world["users"]["admin"])
    _scan(db, org, 72)
    assert _below(admin) == []  # no target

    org.target_score = 85
    db.commit()
    assert [i["kind"] for i in _below(admin)] == ["below_target"]
    _scan(db, org, 85, age_hours=0)  # exactly at the target is met
    assert _below(admin) == []
    org.target_score = 100
    db.commit()
    assert len(_below(admin)) == 1  # the target moved above the latest score again
    org.target_score = 85
    db.commit()
    _scan(db, org, None, age_hours=0)  # an unscored scan (nothing assessed) never counts as below
    assert _below(admin) == []


def test_a_muted_kind_is_left_out(db, world):
    org = world["org"]
    org.target_score = 85
    db.commit()
    _scan(db, org, 50)
    member = _client(db, world["users"]["member"])
    assert member.put("/orgs/acme-ts/notifications/preferences", json={"muted_kinds": ["below_target"]}).status_code == 200
    assert _below(member) == []


def test_dismissing_holds_until_a_newer_scan_is_still_under_the_target(db, world):
    org = world["org"]
    org.target_score = 85
    db.commit()
    first = _scan(db, org, 60, age_hours=3)
    member = _client(db, world["users"]["member"])

    assert member.post(f"/orgs/acme-ts/notifications/below_target:{first}/dismiss").status_code == 204
    assert _below(member)[0]["read"] is True
    second = _scan(db, org, 61, age_hours=0)
    items = _below(member)
    assert [i["id"] for i in items] == [f"below_target:{second}"] and items[0]["read"] is False
    # Only the newest scored scan, and only while it is under the target, is a real item.
    assert member.post(f"/orgs/acme-ts/notifications/below_target:{first}/dismiss").status_code == 404
    org.target_score = 50
    db.commit()
    assert member.post(f"/orgs/acme-ts/notifications/below_target:{second}/dismiss").status_code == 404
