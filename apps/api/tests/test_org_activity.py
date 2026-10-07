"""Tests for GET /orgs/{org}/audit and GET /orgs/{org}/jobs (org admins; tenant-scoped)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, Job, User, get_db
from src.repositories import audit_repo, job_repo, org_membership_repo, org_repo
from src.routers.org_activity import router
from src.schemas.cache import CacheClearInput
from src.services import backfill_service, cache_service, membership_reconcile_service


@pytest.fixture()
def world(db):
    users = {}
    for name in ("acme_admin", "acme_member", "globex_admin"):
        u = User(email=f"{name}@e.com", name=None, password_hash=None, is_workspace_admin=False)
        db.add(u)
        users[name] = u
    db.commit()
    for u in users.values():
        db.refresh(u)
    acme = org_repo.get_or_create(db, github_login="acme")
    globex = org_repo.get_or_create(db, github_login="globex")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["acme_admin"].id, role="admin")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["acme_member"].id, role="member")
    org_membership_repo.get_or_create(db, org_id=globex.id, user_id=users["globex_admin"].id, role="admin")
    return {"users": users, "acme": acme, "globex": globex}


def _client(db, user: User):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    # Overriding require_auth skips its SET app.user_id side effect, which RLS (membership lookups) depends on.
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _audit(db, tenant_id, action, actor="x@e.com", target="acme", when=None):
    audit_repo.write(db, actor, action, target, {"k": "v"}, tenant_id=tenant_id)
    if when is not None:
        row = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        row.created_at = when
        db.commit()
    return db.query(AuditLog).order_by(AuditLog.id.desc()).first().id


def _job(db, tenant_id, job_type="github.clear_actions_cache", status="done", result=None):
    jid = job_repo.enqueue(db, job_type, {"owner": "acme", "token": "ENCRYPTED-SECRET"}, tenant_id=tenant_id)
    if status != "queued":
        db.query(Job).filter(Job.id == jid).update({"status": status, "result": result})
        db.commit()
    return jid


# --- audit ----------------------------------------------------------------


def test_org_admin_sees_only_their_orgs_audit_rows(db, world):
    mine = _audit(db, world["acme"].tenant_id, "token.save")
    _audit(db, world["globex"].tenant_id, "token.save", target="globex")  # another org
    # Rows with no tenant (written before attribution) can't be inserted under RLS, which requires the
    # row's tenant to match the session's; an admin's personal tenant stands in for "not this org".
    personal = org_repo.get_or_create(db, github_login="someone-else").tenant_id
    _audit(db, personal, "config.update")
    _audit(db, personal, "membership.github_granted")

    resp = _client(db, world["users"]["acme_admin"]).get("/orgs/acme/audit")

    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [mine]
    assert set(resp.json()[0]) == {"id", "actor", "action", "target", "payload", "created_at"}  # no tenant_id


def test_a_member_and_another_orgs_admin_are_forbidden(db, world):
    assert _client(db, world["users"]["acme_member"]).get("/orgs/acme/audit").status_code == 403
    assert _client(db, world["users"]["globex_admin"]).get("/orgs/acme/audit").status_code == 403
    assert _client(db, world["users"]["acme_member"]).get("/orgs/acme/jobs").status_code == 403
    assert _client(db, world["users"]["globex_admin"]).get("/orgs/acme/jobs").status_code == 403


def test_unknown_org_is_404(db, world):
    assert _client(db, world["users"]["acme_admin"]).get("/orgs/nosuchorg/audit").status_code == 404


def test_requires_auth(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    assert TestClient(app).get("/orgs/acme/audit").status_code == 401


def test_audit_is_newest_first_and_pages_with_before_id(db, world):
    t = world["acme"].tenant_id
    ids = [_audit(db, t, f"a.{i}") for i in range(5)]
    client = _client(db, world["users"]["acme_admin"])

    first = client.get("/orgs/acme/audit", params={"limit": 2}).json()
    assert [r["id"] for r in first] == [ids[4], ids[3]]
    second = client.get("/orgs/acme/audit", params={"limit": 2, "before_id": first[-1]["id"]}).json()
    assert [r["id"] for r in second] == [ids[2], ids[1]]
    last = client.get("/orgs/acme/audit", params={"limit": 2, "before_id": second[-1]["id"]}).json()
    assert [r["id"] for r in last] == [ids[0]]


def test_action_prefix_matches_literally_not_as_a_wildcard(db, world):
    t = world["acme"].tenant_id
    token_save = _audit(db, t, "token.save")
    token_del = _audit(db, t, "token.delete")
    _audit(db, t, "tokenXsave")  # `_` / `%` in a filter must not behave as LIKE wildcards
    _audit(db, t, "invitation.create")
    client = _client(db, world["users"]["acme_admin"])

    assert {r["id"] for r in client.get("/orgs/acme/audit", params={"action_prefix": "token."}).json()} == {token_save, token_del}
    assert client.get("/orgs/acme/audit", params={"action_prefix": "token_"}).json() == []
    assert client.get("/orgs/acme/audit", params={"action_prefix": "%"}).json() == []


def test_actor_and_target_filters(db, world):
    t = world["acme"].tenant_id
    a = _audit(db, t, "x.y", actor="Alice@Example.com", target="acme/api")
    _audit(db, t, "x.y", actor="bob@example.com", target="acme/web")
    client = _client(db, world["users"]["acme_admin"])

    assert [r["id"] for r in client.get("/orgs/acme/audit", params={"actor": "alice@example.com"}).json()] == [a]
    assert client.get("/orgs/acme/audit", params={"actor": "alice"}).json() == []  # exact, not a substring
    assert [r["id"] for r in client.get("/orgs/acme/audit", params={"target": "/API"}).json()] == [a]
    assert client.get("/orgs/acme/audit", params={"target": "50%"}).json() == []


def test_since_and_until_bound_the_time_range(db, world):
    t = world["acme"].tenant_id
    now = datetime.now(timezone.utc)
    old = _audit(db, t, "x.old", when=now - timedelta(days=10))
    mid = _audit(db, t, "x.mid", when=now - timedelta(days=5))
    new = _audit(db, t, "x.new", when=now - timedelta(days=1))
    client = _client(db, world["users"]["acme_admin"])

    def ids(**params):
        return {r["id"] for r in client.get("/orgs/acme/audit", params=params).json()}

    assert ids(since=(now - timedelta(days=6)).isoformat()) == {mid, new}
    assert ids(until=(now - timedelta(days=6)).isoformat()) == {old}
    assert ids(since=(now - timedelta(days=6)).isoformat(), until=(now - timedelta(days=2)).isoformat()) == {mid}


def test_a_naive_timestamp_is_read_as_utc_and_an_inverted_range_is_rejected(db, world):
    t = world["acme"].tenant_id
    when = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    row = _audit(db, t, "x.at", when=when)
    client = _client(db, world["users"]["acme_admin"])

    ids = lambda **p: {r["id"] for r in client.get("/orgs/acme/audit", params=p).json()}  # noqa: E731
    assert ids(since="2026-01-10T12:00:00") == {row}
    assert ids(until="2026-01-10T12:00:00") == set()
    bad = client.get("/orgs/acme/audit", params={"since": "2026-02-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"})
    assert bad.status_code == 422


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"before_id": 0}])
def test_audit_parameters_are_bounded(db, world, params):
    assert _client(db, world["users"]["acme_admin"]).get("/orgs/acme/audit", params=params).status_code == 422


# --- jobs -----------------------------------------------------------------


def test_org_admin_sees_only_their_orgs_jobs_and_never_the_payload(db, world):
    mine = _job(db, world["acme"].tenant_id, status="failed", result="boom")
    _job(db, world["globex"].tenant_id)
    _job(db, None)  # unattributed

    resp = _client(db, world["users"]["acme_admin"]).get("/orgs/acme/jobs")

    assert resp.status_code == 200
    body = resp.json()
    assert [j["id"] for j in body] == [mine]
    assert set(body[0]) == {"id", "job_type", "status", "result", "created_at", "updated_at"}
    assert "ENCRYPTED-SECRET" not in resp.text  # the payload holds the encrypted GitHub token
    assert body[0]["status"] == "failed" and body[0]["result"] == "boom"


def test_jobs_are_newest_first_and_page_with_before_id(db, world):
    t = world["acme"].tenant_id
    ids = [_job(db, t) for _ in range(4)]
    client = _client(db, world["users"]["acme_admin"])

    first = client.get("/orgs/acme/jobs", params={"limit": 3}).json()
    assert [j["id"] for j in first] == [ids[3], ids[2], ids[1]]
    assert [j["id"] for j in client.get("/orgs/acme/jobs", params={"limit": 3, "before_id": ids[1]}).json()] == [ids[0]]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"before_id": 0}])
def test_job_parameters_are_bounded(db, world, params):
    assert _client(db, world["users"]["acme_admin"]).get("/orgs/acme/jobs", params=params).status_code == 422


# --- jobs are attributed to a tenant when enqueued ------------------------


def test_enqueue_records_the_tenant_and_defaults_to_none(db, world):
    t = world["acme"].tenant_id
    assert db.query(Job).filter(Job.id == job_repo.enqueue(db, "x", {}, tenant_id=t)).one().tenant_id == t
    assert db.query(Job).filter(Job.id == job_repo.enqueue(db, "x", {})).one().tenant_id is None


def test_the_enqueueing_services_attribute_their_jobs(db, world):
    t = world["acme"].tenant_id
    backfill = backfill_service.enqueue(db, tenant_id=t, account_login="acme", account_type="Organization", token="ghp_x")
    reconcile = membership_reconcile_service.enqueue(db, tenant_id=t, org_login="acme", token="ghp_x")
    clear = cache_service.clear(
        db, "acme", "api", CacheClearInput(dry_run=False), actor="a@e.com", token="ghp_x", tenant_id=t
    )["job_id"]

    for job_id in (backfill, reconcile, clear):
        assert db.query(Job).filter(Job.id == job_id).one().tenant_id == t
    # ...and they are exactly what the org's admins can list
    listed = {j["id"] for j in _client(db, world["users"]["acme_admin"]).get("/orgs/acme/jobs").json()}
    assert listed == {backfill, reconcile, clear}
