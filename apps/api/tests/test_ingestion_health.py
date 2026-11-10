"""GET /orgs/{org}/ingestion-health: is GitHub data reaching Clevis? (#813)"""
from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import ActivitySyncCursor, OrgMembershipSyncCursor, User, WebhookDelivery, get_db, set_session_user
from src.repositories import org_membership_repo, org_repo
from src.routers.ingestion_health import router

_ids = count(1)


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
def admin(db, org):
    user = _user(db, "admin@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin")
    return user


def _delivery(db, tenant_id, status: str, age: timedelta):
    db.add(
        WebhookDelivery(
            tenant_id=tenant_id, delivery_id=f"d{next(_ids)}", event_type="push", payload=b"{}", status=status,
            received_at=datetime.now(timezone.utc) - age,
        )
    )
    db.commit()


def test_a_tenant_that_never_received_a_delivery_says_so(db, org, admin):
    body = _client(db, admin).get("/orgs/acme/ingestion-health").json()

    assert body["has_deliveries"] is False
    assert body["last_received_at"] is None and body["last_processed_at"] is None
    assert body["received"] == 0 and body["last_backfill_at"] is None and body["last_membership_reconcile_at"] is None


def test_summarises_the_last_24_hours_by_outcome(db, org, admin):
    t = org.tenant_id
    _delivery(db, t, "processed", timedelta(hours=2))
    _delivery(db, t, "processed", timedelta(hours=1))
    _delivery(db, t, "queue_failed", timedelta(minutes=30))
    _delivery(db, t, "dropped", timedelta(hours=3))
    _delivery(db, t, "queue_abandoned", timedelta(hours=4))
    _delivery(db, t, "queued", timedelta(minutes=5))
    _delivery(db, t, "dropped", timedelta(days=3))  # outside the window, but still proves ingestion has worked

    body = _client(db, admin).get("/orgs/acme/ingestion-health").json()

    assert body["has_deliveries"] is True
    assert (body["received"], body["queue_failed"], body["dropped"], body["queue_abandoned"], body["pending"]) == (6, 1, 1, 1, 1)
    assert body["window_hours"] == 24
    assert body["last_received_at"] > body["last_processed_at"]  # the newest delivery is the pending one
    age = datetime.now(timezone.utc) - datetime.fromisoformat(body["last_processed_at"])
    assert timedelta(minutes=55) < age < timedelta(minutes=65)


def test_reports_the_sync_cursors(db, org, admin):
    backfill = datetime.now(timezone.utc) - timedelta(hours=5)
    reconcile = datetime.now(timezone.utc) - timedelta(hours=1)
    db.add(ActivitySyncCursor(tenant_id=org.tenant_id, account_login="acme", account_type="Organization", last_synced_at=backfill))
    db.add(OrgMembershipSyncCursor(tenant_id=org.tenant_id, org_login="acme", last_synced_at=reconcile))
    db.commit()

    body = _client(db, admin).get("/orgs/acme/ingestion-health").json()

    assert datetime.fromisoformat(body["last_backfill_at"]) == backfill
    assert datetime.fromisoformat(body["last_membership_reconcile_at"]) == reconcile


def test_other_tenants_deliveries_do_not_count(db, org, admin):
    other = org_repo.get_or_create(db, github_login="other-org")
    _delivery(db, other.tenant_id, "processed", timedelta(minutes=1))
    _delivery(db, None, "queued", timedelta(minutes=1))  # tenant not resolved

    assert _client(db, admin).get("/orgs/acme/ingestion-health").json()["has_deliveries"] is False


def test_admin_only(db, org, admin):
    member = _user(db, "member@example.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    assert _client(db, member).get("/orgs/acme/ingestion-health").status_code == 403
