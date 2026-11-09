"""Filters and cursor on the workspace-admin `GET /audit` (migration 0075)."""

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, Tenant, User, get_db
from src.core.rbac import set_tenant_session_context
from src.routers.audit import router as audit_router

_ADMIN = UserOut(id=1, email="admin@example.com", name=None, is_workspace_admin=True)


def _client(db):
    app = FastAPI()
    app.include_router(audit_router, prefix="/audit")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: _ADMIN
    db.execute(text(f"SET app.user_id = {_ADMIN.id}"))
    return TestClient(app)


def _tenant_id(db):
    """audit_logs RLS is strict tenant equality, so seed rows under a real tenant's context (the read itself
    goes through the SECURITY DEFINER function and sees every tenant)."""
    if not hasattr(db, "_audit_tenant_id"):
        user = User(email="audit-filters@test.local", name=None, password_hash=None, is_workspace_admin=False)
        db.add(user)
        db.flush()
        tenant = Tenant(kind="personal", personal_user_id=user.id)
        db.add(tenant)
        db.flush()
        set_tenant_session_context(db, tenant.id, user.id)
        db._audit_tenant_id = tenant.id
    return db._audit_tenant_id


def _row(db, actor, action, target, day):
    row = AuditLog(
        actor=actor, action=action, target=target, payload="{}", tenant_id=_tenant_id(db),
        created_at=datetime(2026, 3, day, 12, tzinfo=timezone.utc),
    )
    db.add(row)
    db.flush()
    return row.id


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()]


def test_filters_by_actor_case_insensitively_and_exactly(db):
    mine = _row(db, "Ada@Example.com", "token.save", "acme", 1)
    _row(db, "ada@example.com.evil", "token.save", "acme", 2)
    _row(db, "bob@example.com", "token.save", "acme", 3)
    assert _ids(_client(db).get("/audit", params={"actor": "ada@example.com"})) == [mine]


def test_filters_by_target_substring_and_treats_like_wildcards_literally(db):
    hit = _row(db, "a", "x", "acme/api", 1)
    _row(db, "a", "x", "acme-web", 2)
    pct = _row(db, "a", "x", "100%_done", 3)
    client = _client(db)
    assert _ids(client.get("/audit", params={"target": "acme/"})) == [hit]
    # "%" and "_" in the filter are literal, not wildcards.
    assert _ids(client.get("/audit", params={"target": "0%_d"})) == [pct]


def test_filters_by_date_range_with_exclusive_until(db):
    _row(db, "a", "x", "t", 1)
    mid = _row(db, "a", "x", "t", 5)
    _row(db, "a", "x", "t", 9)
    resp = _client(db).get(
        "/audit", params={"since": "2026-03-05T00:00:00Z", "until": "2026-03-06T00:00:00Z"}
    )
    assert _ids(resp) == [mid]


def test_since_not_before_until_is_rejected(db):
    resp = _client(db).get("/audit", params={"since": "2026-03-05T00:00:00Z", "until": "2026-03-05T00:00:00Z"})
    assert resp.status_code == 422


def test_before_id_cursor_pages_past_the_first_page_without_overlap(db):
    ids = [_row(db, "a", "x", "t", d) for d in range(1, 6)]
    client = _client(db)
    first = _ids(client.get("/audit", params={"limit": 2, "actor": "a"}))
    assert first == [ids[4], ids[3]]
    second = _ids(client.get("/audit", params={"limit": 2, "actor": "a", "before_id": first[-1]}))
    assert second == [ids[2], ids[1]]


def test_action_filter_still_exact_and_combines_with_the_new_filters(db):
    keep = _row(db, "a", "token.save", "t", 1)
    _row(db, "a", "token.delete", "t", 2)
    _row(db, "b", "token.save", "t", 3)
    assert _ids(_client(db).get("/audit", params={"action": "token.save", "actor": "a"})) == [keep]


def test_limit_is_still_capped_at_500(db):
    assert _client(db).get("/audit", params={"limit": 501}).status_code == 422


def test_a_non_positive_limit_is_a_422_not_a_500(db):
    client = _client(db)
    for limit in (0, -1, -5):
        assert client.get("/audit", params={"limit": limit}).status_code == 422


def test_a_before_id_cursor_beyond_the_integer_range_is_rejected_not_a_500(db):
    resp = _client(db).get("/audit", params={"before_id": 2**31})
    assert resp.status_code == 422


def test_before_id_beyond_the_database_integer_range_is_rejected_not_a_server_error(db):
    assert _client(db).get("/audit", params={"before_id": 2_147_483_648}).status_code == 422


def test_filters_by_action_prefix_and_treats_like_wildcards_literally(db):
    """Filters by action prefix and treats like wildcards literally."""
    save = _row(db, "a", "token.save", "t", 1)
    delete = _row(db, "a", "token.delete", "t", 2)
    _row(db, "a", "cache.clear.queued", "t", 3)
    odd = _row(db, "a", "50%_off.apply", "t", 4)
    client = _client(db)
    assert _ids(client.get("/audit", params={"action_prefix": "token."})) == [delete, save]
    # "%" and "_" in the prefix are literal, not wildcards.
    assert _ids(client.get("/audit", params={"action_prefix": "0%_"})) == []
    assert _ids(client.get("/audit", params={"action_prefix": "50%_"})) == [odd]


def test_action_prefix_combines_with_exact_action_and_the_cursor(db):
    """Action prefix combines with exact action and the cursor."""
    ids = [_row(db, "a", "token.save", "t", d) for d in range(1, 5)]
    _row(db, "a", "cache.clear.queued", "t", 5)
    client = _client(db)
    assert _ids(client.get("/audit", params={"action_prefix": "token.", "action": "token.delete"})) == []
    first = _ids(client.get("/audit", params={"action_prefix": "token.", "limit": 2}))
    assert first == [ids[3], ids[2]]
    assert _ids(client.get("/audit", params={"action_prefix": "token.", "limit": 2, "before_id": first[-1]})) == [
        ids[1],
        ids[0],
    ]


def test_cursor_combines_with_target_action_and_date_filters(db):
    """Cursor combines with target action and date filters."""
    old = _row(db, "a", "token.save", "acme/api", 2)
    mid = _row(db, "a", "token.save", "acme/api", 5)
    new = _row(db, "a", "token.save", "acme/api", 8)
    _row(db, "a", "token.delete", "acme/api", 6)
    _row(db, "a", "token.save", "other", 7)
    client = _client(db)
    params = {"action": "token.save", "target": "acme/", "since": "2026-03-01T00:00:00Z", "until": "2026-03-20T00:00:00Z"}
    assert _ids(client.get("/audit", params={**params, "before_id": new})) == [mid, old]
    assert _ids(client.get("/audit", params={**params, "before_id": new, "limit": 500})) == [mid, old]


def test_a_timestamp_without_an_offset_is_read_as_utc(db):
    """A timestamp without an offset is read as utc."""
    _row(db, "a", "x", "t", 1)
    inside = _row(db, "a", "x", "t", 5)
    resp = _client(db).get("/audit", params={"since": "2026-03-05T00:00:00", "until": "2026-03-06T00:00:00"})
    assert _ids(resp) == [inside]


def test_actions_lists_every_distinct_action(db):
    """Actions lists every distinct action."""
    _row(db, "a", "token.save", "t", 1)
    _row(db, "a", "token.save", "t", 2)
    _row(db, "a", "cache.clear.queued", "t", 3)
    resp = _client(db).get("/audit/actions")
    assert resp.status_code == 200
    assert {"token.save", "cache.clear.queued"} <= set(resp.json())


def test_a_non_admin_is_refused_with_filters_too(db):
    """A non admin is refused with filters too."""
    app = FastAPI()
    app.include_router(audit_router, prefix="/audit")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: UserOut(id=2, email="m@example.com", name=None, is_workspace_admin=False)
    client = TestClient(app)
    assert client.get("/audit", params={"actor": "a", "action_prefix": "token."}).status_code == 403
    assert client.get("/audit/actions").status_code == 403
