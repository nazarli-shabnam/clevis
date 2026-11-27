"""Waiver requests (#1274): a member asks, an org admin approves (creating the waiver) or denies."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, CheckWaiver, User, WaiverRequest, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.org_notifications import router as notifications_router
from src.routers.waivers import router

NOW = datetime.now(timezone.utc)
BP = "repository_default_branch_protection_enabled"
MFA = "organization_members_mfa_required"


@pytest.fixture()
def world(db):
    users = {}
    for name in ("admin", "member", "other", "outsider"):
        u = User(email=f"{name}@wr.com", name=None, password_hash=None, is_workspace_admin=False)
        db.add(u)
        users[name] = u
    db.commit()
    for u in users.values():
        db.refresh(u)
    acme = org_repo.get_or_create(db, github_login="acme-wr")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["admin"].id, role="admin")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["member"].id, role="member")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["other"].id, role="member")
    return {"users": users, "org": acme}


def _client(db, user):
    app = FastAPI()
    app.include_router(router)
    app.include_router(notifications_router)
    app.dependency_overrides[require_auth] = lambda: UserOut(id=user.id, email=user.email, name=None, is_workspace_admin=False)
    app.dependency_overrides[get_db] = lambda: db
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _body(**over):
    return {"check_id": BP, "repos": ["legacy"], "reason": "migrating off", "expires_at": (NOW + timedelta(days=30)).isoformat(), **over}


def _ask(db, world, who="member", **over):
    return _client(db, world["users"][who]).post("/orgs/acme-wr/waiver-requests", json=_body(**over))


def test_a_member_can_request_and_it_is_audited_and_changes_nothing(db, world):
    resp = _ask(db, world)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending" and body["requested_by"] == "member@wr.com" and body["repos"] == ["legacy"]
    assert db.query(CheckWaiver).count() == 0  # nothing is waived until an admin approves
    audit = db.query(AuditLog).filter(AuditLog.action == "waiver.request").one()
    assert audit.actor == "member@wr.com" and json.loads(audit.payload)["check_id"] == BP


def test_an_outsider_cannot_request(db, world):
    assert _ask(db, world, who="outsider").status_code == 403


@pytest.mark.parametrize(
    "over,fragment",
    [
        ({"check_id": "nope"}, "Unknown check"),
        ({"expires_at": (NOW - timedelta(days=1)).isoformat()}, "future"),
        ({"expires_at": (NOW + timedelta(days=400)).isoformat()}, "at most"),
        ({"check_id": MFA, "repos": ["a"]}, "whole organization"),
    ],
)
def test_a_request_is_validated_like_a_waiver(db, world, over, fragment):
    resp = _ask(db, world, **over)
    assert resp.status_code == 422 and fragment in resp.text


def test_pending_requests_are_capped_per_user(db, world):
    for _ in range(5):
        assert _ask(db, world).status_code == 201
    assert _ask(db, world).status_code == 409
    assert _ask(db, world, who="other").status_code == 201  # another member is unaffected


def test_members_see_only_their_own_requests_and_admins_see_all(db, world):
    _ask(db, world, who="member")
    _ask(db, world, who="other")

    mine = _client(db, world["users"]["member"]).get("/orgs/acme-wr/waiver-requests").json()
    everything = _client(db, world["users"]["admin"]).get("/orgs/acme-wr/waiver-requests").json()
    pending = _client(db, world["users"]["admin"]).get("/orgs/acme-wr/waiver-requests?status=denied").json()

    assert [r["requested_by"] for r in mine] == ["member@wr.com"]
    assert sorted(r["requested_by"] for r in everything) == ["member@wr.com", "other@wr.com"]
    assert pending == []


def test_approving_creates_the_waiver_and_closes_the_request(db, world):
    rid = _ask(db, world).json()["id"]
    admin = _client(db, world["users"]["admin"])

    resp = admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved" and body["decided_by"] == "admin@wr.com" and body["waiver_id"]
    waiver = db.query(CheckWaiver).one()
    assert (waiver.check_id, waiver.repos, waiver.owner, waiver.created_by) == (BP, ["legacy"], "member@wr.com", "admin@wr.com")
    assert db.query(AuditLog).filter(AuditLog.action == "waiver.request_approve").count() == 1
    assert db.query(AuditLog).filter(AuditLog.action == "waiver.create").count() == 1
    # A decided request cannot be decided again.
    assert admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={}).status_code == 409
    assert admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/deny", json={}).status_code == 409


def test_the_admin_can_change_owner_and_expiry_when_approving(db, world):
    rid = _ask(db, world).json()["id"]
    later = (NOW + timedelta(days=10)).isoformat()

    resp = _client(db, world["users"]["admin"]).post(
        f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={"owner": "platform team", "expires_at": later}
    )

    assert resp.status_code == 200
    waiver = db.query(CheckWaiver).one()
    assert waiver.owner == "platform team" and abs((waiver.expires_at - datetime.fromisoformat(later)).total_seconds()) < 1


def test_a_request_whose_expiry_has_passed_needs_a_new_date_to_approve(db, world):
    rid = _ask(db, world).json()["id"]
    db.query(WaiverRequest).filter(WaiverRequest.id == rid).update({"requested_expires_at": NOW - timedelta(days=1)})
    db.commit()
    admin = _client(db, world["users"]["admin"])

    assert admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={}).status_code == 422
    ok = admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={"expires_at": (NOW + timedelta(days=5)).isoformat()})
    assert ok.status_code == 200


def test_denying_records_the_decision_and_creates_no_waiver(db, world):
    rid = _ask(db, world).json()["id"]

    resp = _client(db, world["users"]["admin"]).post(f"/orgs/acme-wr/waiver-requests/{rid}/deny", json={"note": " not now "})

    assert resp.status_code == 200
    assert resp.json()["status"] == "denied" and resp.json()["decision_note"] == "not now"
    assert db.query(CheckWaiver).count() == 0
    assert db.query(AuditLog).filter(AuditLog.action == "waiver.request_deny").count() == 1


def test_only_admins_decide_and_unknown_requests_404(db, world):
    rid = _ask(db, world).json()["id"]
    member = _client(db, world["users"]["member"])

    assert member.post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={}).status_code == 403
    assert member.post(f"/orgs/acme-wr/waiver-requests/{rid}/deny", json={}).status_code == 403
    assert _client(db, world["users"]["admin"]).post("/orgs/acme-wr/waiver-requests/999999/approve", json={}).status_code == 404


def test_revoking_the_approved_waiver_keeps_the_request_record(db, world):
    rid = _ask(db, world).json()["id"]
    admin = _client(db, world["users"]["admin"])
    waiver_id = admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={}).json()["waiver_id"]

    assert admin.delete(f"/orgs/acme-wr/waivers/{waiver_id}").status_code == 204

    request = db.query(WaiverRequest).filter(WaiverRequest.id == rid).one()
    db.refresh(request)
    assert request.status == "approved" and request.waiver_id is None


def test_the_feed_shows_pending_requests_to_admins_only_until_decided(db, world):
    rid = _ask(db, world).json()["id"]
    admin = _client(db, world["users"]["admin"])
    member = _client(db, world["users"]["member"])

    items = [i for i in admin.get("/orgs/acme-wr/notifications").json()["items"] if i["kind"] == "waiver_request"]
    assert [i["id"] for i in items] == [f"waiver_request:{rid}"]
    assert "member@wr.com asked to waive" in items[0]["title"] and items[0]["href"].endswith(f"/settings?waiver_request={rid}")
    assert not [i for i in member.get("/orgs/acme-wr/notifications").json()["items"] if i["kind"] == "waiver_request"]
    assert admin.post(f"/orgs/acme-wr/notifications/waiver_request:{rid}/dismiss").status_code == 204

    admin.post(f"/orgs/acme-wr/waiver-requests/{rid}/deny", json={})
    assert not [i for i in admin.get("/orgs/acme-wr/notifications").json()["items"] if i["kind"] == "waiver_request"]
    # A decided request is no longer a real feed item, so it cannot be dismissed.
    assert admin.post(f"/orgs/acme-wr/notifications/waiver_request:{rid}/dismiss").status_code == 404


def test_a_very_long_requester_email_still_approves_with_a_trimmed_owner(db, world):
    rid = _ask(db, world).json()["id"]
    db.query(WaiverRequest).filter(WaiverRequest.id == rid).update({"requested_by": "x" * 150 + "@wr.com"})
    db.commit()

    resp = _client(db, world["users"]["admin"]).post(f"/orgs/acme-wr/waiver-requests/{rid}/approve", json={})

    assert resp.status_code == 200, resp.text
    assert len(db.query(CheckWaiver).one().owner) == 100
