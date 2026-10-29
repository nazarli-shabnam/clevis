"""Check waivers: CRUD + audit, score integration, expiry announcement, notification item."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, CheckWaiver, User, get_db
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers.org_notifications import router as notifications_router
from src.routers.waivers import router
from src.services import scan_service, waivers as waiver_service
from src.services.waiver_expiry_sweep import run_waiver_expiry_sweep

NOW = datetime.now(timezone.utc)
BP = "repository_default_branch_protection_enabled"
MFA = "organization_members_mfa_required"
SECRETS = "repository_secret_scanning_enabled"


@pytest.fixture()
def world(db):
    users = {}
    for name in ("admin", "member", "outsider"):
        u = User(email=f"{name}@w.com", name=None, password_hash=None, is_workspace_admin=False)
        db.add(u)
        users[name] = u
    db.commit()
    for u in users.values():
        db.refresh(u)
    acme = org_repo.get_or_create(db, github_login="acme-w")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["admin"].id, role="admin")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["member"].id, role="member")
    return {"users": users, "org": acme}


def _client(db, user, *routers):
    app = FastAPI()
    for r in routers or (router,):
        app.include_router(r)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _body(check_id=BP, days=30, repos=None, **over):
    return {
        "check_id": check_id,
        "repos": repos,
        "reason": "legacy repo, migrating in Q1",
        "owner": "platform-team",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
        **over,
    }


def _waiver(db, org, check_id=BP, repos=None, days=30, **over):
    fields = {"reason": "r", "owner": "o", "created_by": "t@e.com", **over}
    w = CheckWaiver(
        tenant_id=org.tenant_id, check_id=check_id, repos=repos, expires_at=NOW + timedelta(days=days), **fields
    )
    db.add(w)
    db.commit()
    return w


def _result(*checks):
    return {
        "owner": "acme-w", "score": None, "total_checks": 0, "failed_checks": 0,
        "checks": [dict({"scored": True, "value": {}}, **c) for c in checks],
    }


# --- scoring ------------------------------------------------------------------------------------

def test_org_wide_waiver_excludes_a_failing_check_from_the_score_but_keeps_it_visible(db, world):
    _waiver(db, world["org"], BP)
    result = _result({"id": BP, "status": "fail"}, {"id": MFA, "status": "pass"})
    waiver_service.apply_to_result(db, world["org"].tenant_id, result)
    waived = result["checks"][0]
    assert waived["waived"]["owner"] == "o" and waived["waived"]["reason"] == "r" and waived["scored"] is False
    assert (result["total_checks"], result["failed_checks"], result["score"]) == (1, 0, 100)


def test_a_passing_check_is_not_waived(db, world):
    _waiver(db, world["org"], MFA)
    result = _result({"id": MFA, "status": "pass"}, {"id": BP, "status": "fail"})
    waiver_service.apply_to_result(db, world["org"].tenant_id, result)
    assert "waived" not in result["checks"][0]
    assert "waived" not in result["checks"][1]


def test_expired_waivers_do_not_apply(db, world):
    _waiver(db, world["org"], BP, days=-1)
    result = _result({"id": BP, "status": "fail"})
    waiver_service.apply_to_result(db, world["org"].tenant_id, result)
    assert "waived" not in result["checks"][0]


def test_repo_scoped_waiver_applies_only_when_every_failing_repo_is_covered(db, world):
    _waiver(db, world["org"], BP, repos=["legacy", "old-api"])
    covered = _result({"id": BP, "status": "fail", "value": {"repos": ["legacy"]}})
    waiver_service.apply_to_result(db, world["org"].tenant_id, covered)
    assert covered["checks"][0]["waived"]["repos"] == ["legacy"]
    assert covered["score"] is None  # nothing is left to score

    extra = _result({"id": BP, "status": "fail", "value": {"repos": ["legacy", "new-service"]}})
    waiver_service.apply_to_result(db, world["org"].tenant_id, extra)
    assert "waived" not in extra["checks"][0]

    # A failure with no named repos, or an errored one, can't be proven covered.
    for check in (
        {"id": BP, "status": "fail", "value": {}},
        {"id": BP, "status": "error", "value": {"repos": ["legacy"]}},
    ):
        r = _result(check)
        waiver_service.apply_to_result(db, world["org"].tenant_id, r)
        assert "waived" not in r["checks"][0]


def test_repo_scoped_waiver_does_not_apply_to_a_possibly_truncated_repo_list(db, world):
    names = [f"r{i:02d}" for i in range(25)]  # MAX_AFFECTED_REPOS: the real list may be longer
    _waiver(db, world["org"], BP, repos=names)
    r = _result({"id": BP, "status": "fail", "value": {"repos": names}})
    waiver_service.apply_to_result(db, world["org"].tenant_id, r)
    assert "waived" not in r["checks"][0]


def test_persist_scan_applies_waivers_so_the_stored_score_matches_the_response(db, world):
    org = world["org"]
    _waiver(db, org, BP)
    result = _result({"id": BP, "status": "fail", "title": "t"}, {"id": MFA, "status": "pass", "title": "m"})
    scan_service.persist_scan(db, result, tenant_id=org.tenant_id)
    stored = scan_results_repo.latest_with_checks(db, "acme-w", org.tenant_id)
    assert result["score"] == stored["score"] == 100
    assert stored["checks"][0]["waived"]["owner"] == "o"


def test_a_scan_with_a_waiver_is_not_compared_with_one_without(db, world):
    org = world["org"]
    first = _result({"id": BP, "status": "pass"}, {"id": MFA, "status": "pass"})
    scan_service.persist_scan(db, first, tenant_id=org.tenant_id)
    _waiver(db, org, BP)
    second = _result({"id": BP, "status": "fail"}, {"id": MFA, "status": "pass"})
    scan_service.persist_scan(db, second, tenant_id=org.tenant_id)
    assert second["checks"][0]["waived"]["owner"] == "o"
    # A different scored set is not a real score change, so no drop alert is raised for it.
    assert not scan_service.comparable_scans({"checks": first["checks"]}, {"checks": second["checks"]})


# --- API ----------------------------------------------------------------------------------------

def test_admin_creates_edits_and_revokes_a_waiver_with_audit_rows(db, world):
    admin = _client(db, world["users"]["admin"])
    created = admin.post("/orgs/acme-w/waivers", json=_body(repos=["legacy", "LEGACY"]))
    assert created.status_code == 201
    w = created.json()
    assert w["repos"] == ["legacy"] and w["active"] is True and w["created_by"] == "admin@w.com"

    updated = admin.put(f"/orgs/acme-w/waivers/{w['id']}", json=_body(check_id=SECRETS, days=10))
    assert updated.status_code == 200 and updated.json()["check_id"] == SECRETS and updated.json()["repos"] is None

    assert admin.delete(f"/orgs/acme-w/waivers/{w['id']}").status_code == 204
    assert admin.get("/orgs/acme-w/waivers").json() == []
    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.action.like("waiver.%")).order_by(AuditLog.id)]
    assert actions == ["waiver.create", "waiver.update", "waiver.revoke"]


def test_members_can_read_waivers_but_not_change_them(db, world):
    _waiver(db, world["org"], BP)
    member = _client(db, world["users"]["member"])
    assert [w["check_id"] for w in member.get("/orgs/acme-w/waivers").json()] == [BP]
    assert member.post("/orgs/acme-w/waivers", json=_body()).status_code == 403
    assert member.put("/orgs/acme-w/waivers/1", json=_body()).status_code == 403
    assert member.delete("/orgs/acme-w/waivers/1").status_code == 403
    assert _client(db, world["users"]["outsider"]).get("/orgs/acme-w/waivers").status_code in (403, 404)


def test_an_errored_check_is_never_waived_it_stays_scored(db, world):
    _waiver(db, world["org"], BP)  # org-wide
    result = _result({"id": BP, "status": "error"}, {"id": MFA, "status": "pass"})
    waiver_service.apply_to_result(db, world["org"].tenant_id, result)
    assert "waived" not in result["checks"][0] and result["checks"][0]["scored"] is True
    assert (result["total_checks"], result["failed_checks"]) == (0, 0)  # untouched: nothing was recomputed
    from src.services.analytics_service import tally

    assert tally(result["checks"]) == (2, 1, 50)  # the error still counts against the score


def test_the_stamp_names_every_contributing_waiver(db, world):
    _waiver(db, world["org"], BP, repos=["a"], owner="team-a", reason="legacy a")
    _waiver(db, world["org"], BP, repos=["b"], owner="team-b", reason="legacy b")
    result = _result({"id": BP, "status": "fail", "value": {"repos": ["a", "b"]}})
    waiver_service.apply_to_result(db, world["org"].tenant_id, result)
    stamp = result["checks"][0]["waived"]
    assert stamp["owner"] == "team-a; team-b" and stamp["reason"] == "legacy a; legacy b"
    assert len(stamp["waiver_ids"]) == 2


def test_a_naive_expiry_is_read_as_utc_not_the_database_timezone(db, world):
    admin = _client(db, world["users"]["admin"])
    naive = (datetime.now(timezone.utc) + timedelta(days=10)).replace(tzinfo=None).isoformat()
    resp = admin.post("/orgs/acme-w/waivers", json=_body(expires_at=naive))
    assert resp.status_code == 201
    stored = db.query(CheckWaiver).one().expires_at
    assert stored.tzinfo is not None and stored.utcoffset() == timedelta(0)


def test_creating_waivers_takes_the_per_tenant_lock_before_counting(db, world):
    admin = _client(db, world["users"]["admin"])
    statements = []
    from sqlalchemy import event

    def spy(conn, cursor, statement, *a):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", spy)
    try:
        assert admin.post("/orgs/acme-w/waivers", json=_body()).status_code == 201
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", spy)
    lock = next(i for i, s in enumerate(statements) if "pg_advisory_xact_lock" in s and "check_waivers" in s)
    count = next(i for i, s in enumerate(statements) if "FROM check_waivers" in s and "SELECT" in s)
    assert lock < count


def test_waiver_validation(db, world):
    admin = _client(db, world["users"]["admin"])
    assert admin.post("/orgs/acme-w/waivers", json=_body(repos=["acme-w/legacy"])).status_code == 422
    assert admin.post("/orgs/acme-w/waivers", json=_body(check_id="made_up")).status_code == 422
    assert admin.post("/orgs/acme-w/waivers", json=_body(days=-1)).status_code == 422
    too_long = admin.post("/orgs/acme-w/waivers", json=_body(days=91))
    assert too_long.status_code == 422 and "90 days" in too_long.json()["detail"]
    assert admin.post("/orgs/acme-w/waivers", json=_body(reason="   ")).status_code == 422
    assert admin.post("/orgs/acme-w/waivers", json=_body(days=89)).status_code == 201


def test_waivers_are_tenant_scoped(db, world):
    other = org_repo.get_or_create(db, github_login="globex-w")
    # Read the id before committing: the commit expires the instance, and a reload would run outside
    # the other tenant's context.
    foreign = CheckWaiver(
        tenant_id=other.tenant_id, check_id=BP, repos=None, reason="r", owner="o",
        expires_at=NOW + timedelta(days=30), created_by="t@e.com",
    )
    db.add(foreign)
    db.flush()
    foreign_id = foreign.id
    db.commit()
    admin = _client(db, world["users"]["admin"])
    assert admin.get("/orgs/acme-w/waivers").json() == []
    assert admin.delete(f"/orgs/acme-w/waivers/{foreign_id}").status_code == 404


# --- expiry -------------------------------------------------------------------------------------

def test_expiry_sweep_announces_a_lapsed_waiver_exactly_once(db, world):
    w = _waiver(db, world["org"], BP, days=-1)
    with patch("src.services.waiver_expiry_sweep.notifications.notify_waiver_expired") as notify:
        run_waiver_expiry_sweep(db)
        run_waiver_expiry_sweep(db)
    assert notify.call_count == 1
    assert notify.call_args.args[3][0]["check_id"] == BP
    db.refresh(w)
    assert w.expiry_noted_at is not None
    assert db.query(AuditLog).filter(AuditLog.action == "waiver.expired").count() == 1


def test_expiry_sweep_ignores_active_waivers(db, world):
    _waiver(db, world["org"], BP, days=5)
    with patch("src.services.waiver_expiry_sweep.notifications.notify_waiver_expired") as notify:
        run_waiver_expiry_sweep(db)
    notify.assert_not_called()


def test_a_renewed_waiver_can_lapse_and_be_announced_again(db, world):
    w = _waiver(db, world["org"], BP, days=-1, expiry_noted_at=NOW)
    admin = _client(db, world["users"]["admin"])
    admin.put(f"/orgs/acme-w/waivers/{w.id}", json=_body(days=5))
    db.refresh(w)
    assert w.expiry_noted_at is None


# --- notification center ------------------------------------------------------------------------

def test_a_lapsed_waiver_shows_in_the_admins_notifications_only(db, world):
    _waiver(db, world["org"], BP, days=-2)
    admin = _client(db, world["users"]["admin"], notifications_router).get("/orgs/acme-w/notifications").json()
    member = _client(db, world["users"]["member"], notifications_router).get("/orgs/acme-w/notifications").json()
    assert [i["kind"] for i in admin["items"]] == ["waiver_expired"] and admin["unread_count"] == 1
    assert member["items"] == []
