"""Tests for org-scoped and personal installation endpoints."""

import json
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, Job, User, get_db
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.routers.installations import router as inst_router
from src.services import github_app

_OUTSIDER = UserOut(id=99999, email="outsider@e.com", name=None, is_workspace_admin=False)


def _mock_installation(account_login: str, account_type: str):
    return patch(
        "src.routers.installations.github_app.get_installation",
        return_value={"account": {"login": account_login, "type": account_type}},
    )


def _make_user(db, email: str, github_login: str | None = None) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, github_login=github_login)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    # Overriding require_auth skips its SET app.user_id side effect that RLS depends on; set it directly.
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


@pytest.fixture()
def acme_org(db):
    admin = _make_user(db, "admin@e.com")
    member = _make_user(db, "member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def test_list_org_installations_empty(db, acme_org):
    resp = _client(db, acme_org["admin"]).get("/orgs/acme/installations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_org_installations_returns_rows(db, acme_org):
    installation_repo.create(
        db,
        account_login="acme",
        account_type="Organization",
        auth_mode="app",
        installation_id=42,
        org_id=acme_org["org"].id,
    )
    resp = _client(db, acme_org["member"]).get("/orgs/acme/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_login"] == "acme"


def test_list_org_installations_outsider_forbidden(db, acme_org):
    resp = _client(db, _OUTSIDER).get("/orgs/acme/installations")
    assert resp.status_code == 403


def test_list_org_installations_requires_auth(db, acme_org):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get("/orgs/acme/installations")
    assert resp.status_code == 401


def test_sync_org_installation_requires_admin(db, acme_org):
    resp = _client(db, acme_org["member"]).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
    )
    assert resp.status_code == 403


def test_sync_org_installation_admin_ok(db, acme_org):
    with _mock_installation("acme", "Organization"):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True


def test_sync_org_installation_writes_audit_log(db, acme_org):
    # Connecting an installation must write an audit entry.
    with _mock_installation("acme", "Organization"):
        _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    logs = db.query(AuditLog).filter(AuditLog.action == "installation.connected").all()
    assert len(logs) == 1
    assert logs[0].target == "acme"
    assert logs[0].actor == acme_org["admin"].email
    payload = json.loads(logs[0].payload)
    assert payload == {"account_type": "Organization", "installation_id": 7}


def test_sync_org_installation_enqueues_a_backfill_job_when_a_token_is_available(db, acme_org):
    # A successful sync enqueues a one-shot activity backfill. No App is configured in tests,
    # so resolve_org_token is mocked where installations.py imported it.
    with _mock_installation("acme", "Organization"), patch(
        "src.routers.installations.resolve_org_token", return_value="tok_acme"
    ):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 200
    jobs = db.query(Job).filter(Job.job_type == "github.backfill_repo_events").all()
    assert len(jobs) == 1
    payload = json.loads(jobs[0].payload)
    assert payload["account_login"] == "acme"
    assert payload["account_type"] == "Organization"
    assert payload["tenant_id"] == acme_org["org"].tenant_id
    assert payload["token"] != "tok_acme"  # Fernet-encrypted, not the raw token


def test_sync_org_installation_still_succeeds_when_no_backfill_token_is_available(db, acme_org):
    # No App configured -> NoGitHubTokenAvailable; the install must still succeed since backfill is best-effort.
    with _mock_installation("acme", "Organization"):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 200
    assert db.query(Job).filter(Job.job_type == "github.backfill_repo_events").count() == 0


def test_sync_org_installation_survives_a_db_error_during_backfill_enqueue(db, acme_org):
    # A DB error in resolve_token()/enqueue() must not leave the Session aborted, or the
    # response's post-commit lazy reload of row.token_ref turns a successful install into a 500.
    def _boom(*args, **kwargs):
        db.execute(text("SELECT 1/0"))  # forces a real, session-aborting Postgres error

    with _mock_installation("acme", "Organization"), patch(
        "src.routers.installations.resolve_org_token", return_value="tok_acme"
    ), patch("src.routers.installations.backfill_service.enqueue", side_effect=_boom):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True


def test_personal_installations_scoped_to_self(db):
    me = _make_user(db, "shabnam@e.com")
    someone_else = _make_user(db, "someoneelse@e.com")
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=1, owner_user_id=me.id
    )
    installation_repo.create(
        db,
        account_login="someoneelse",
        account_type="User",
        auth_mode="app",
        installation_id=2,
        owner_user_id=someone_else.id,
    )
    resp = _client(db, me).get("/me/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_login"] == "shabnam"


def test_sync_personal_installation(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with _mock_installation("shabnam", "User"):
        resp = _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
        )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True


def test_sync_personal_installation_writes_audit_log(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with _mock_installation("shabnam", "User"):
        _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
        )
    logs = db.query(AuditLog).filter(AuditLog.action == "installation.connected.personal").all()
    assert len(logs) == 1
    assert logs[0].target == "shabnam"
    assert logs[0].actor == me.email
    payload = json.loads(logs[0].payload)
    assert payload == {"account_type": "User", "installation_id": 3}


def test_sync_personal_installation_enqueues_a_backfill_job_when_a_token_is_available(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with _mock_installation("shabnam", "User"), patch(
        "src.routers.installations.resolve_personal_token", return_value="tok_shabnam"
    ):
        resp = _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
        )
    assert resp.status_code == 200
    jobs = db.query(Job).filter(Job.job_type == "github.backfill_repo_events").all()
    assert len(jobs) == 1
    payload = json.loads(jobs[0].payload)
    assert payload["account_login"] == "shabnam"
    assert payload["account_type"] == "User"


def test_sync_personal_installation_requires_linked_github_account(db):
    me = _make_user(db, "unlinked@e.com")
    resp = _client(db, me).post(
        "/me/installations/sync",
        json={"account_login": "someone-else", "account_type": "User", "installation_id": 3},
    )
    assert resp.status_code == 403


def test_sync_personal_installation_login_mismatch_forbidden(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    resp = _client(db, me).post(
        "/me/installations/sync",
        json={"account_login": "someone-else", "account_type": "User", "installation_id": 3},
    )
    assert resp.status_code == 403


def test_sync_org_installation_upserts_existing_row(db, acme_org):
    client = _client(db, acme_org["admin"])
    payload = {"account_login": "acme", "account_type": "Organization", "installation_id": 7}
    with _mock_installation("acme", "Organization"):
        assert client.post("/orgs/acme/installations/sync", json=payload).status_code == 200
        payload["installation_id"] = 8
        assert client.post("/orgs/acme/installations/sync", json=payload).status_code == 200
    rows = client.get("/orgs/acme/installations").json()
    assert len(rows) == 1
    assert rows[0]["installation_id"] == 8


def test_sync_org_installation_rejects_installation_id_owned_by_a_different_account(db, acme_org):
    with _mock_installation("someone-else", "Organization"):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 422
    assert installation_repo.list_for_org(db, org_id=acme_org["org"].id) == []


def test_sync_org_installation_rejects_nonexistent_installation_id(db, acme_org):
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/app/installations/7"))
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=httpx.HTTPStatusError("not found", request=response.request, response=response),
    ):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 422
    assert installation_repo.list_for_org(db, org_id=acme_org["org"].id) == []


def test_sync_org_installation_returns_503_when_app_not_configured(db, acme_org):
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=github_app.GitHubAppNotConfigured("not configured"),
    ):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 503
    assert installation_repo.list_for_org(db, org_id=acme_org["org"].id) == []


def test_sync_org_installation_returns_400_on_other_github_api_error(db, acme_org):
    response = httpx.Response(500, request=httpx.Request("GET", "https://api.github.com/app/installations/7"))
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=httpx.HTTPStatusError("server error", request=response.request, response=response),
    ):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 400
    assert installation_repo.list_for_org(db, org_id=acme_org["org"].id) == []


def test_sync_org_installation_returns_503_on_github_network_error(db, acme_org):
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=httpx.ConnectError("connection refused"),
    ):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 503
    assert installation_repo.list_for_org(db, org_id=acme_org["org"].id) == []


def test_sync_org_installation_bootstraps_new_org_for_live_github_admin(db):
    """First-ever connection by a live-verified GitHub org admin creates the Org + admin membership instead of 404ing."""
    me = _make_user(db, "founder@e.com", github_login="founder")
    with (
        _mock_installation("acme", "Organization"),
        patch("src.routers.installations.github_app.get_installation_token", return_value="itok"),
        patch("src.routers.installations.github_app.get_org_membership_role", return_value="admin") as mock_role,
    ):
        resp = _client(db, me).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 200
    assert resp.json()["synced"] is True
    mock_role.assert_called_once_with("itok", "acme", "founder")
    org = org_repo.get_by_login(db, "acme")
    assert org is not None
    membership = org_membership_repo.get(db, org.id, me.id)
    assert membership is not None
    assert membership.role == "admin"


def test_bootstrap_org_admin_advisory_lock_serializes_concurrent_holders(_engine):
    # Two concurrent syncs for the same org_login could both bootstrap Org/membership rows;
    # a second connection must not acquire the hashtext(org_login) lock until the first releases it.
    from sqlalchemy import text

    with _engine.connect() as conn1, _engine.connect() as conn2:
        conn1.begin()
        conn2.begin()
        try:
            got1 = conn1.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:org_login))"), {"org_login": "acme"}
            ).scalar()
            got2 = conn2.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:org_login))"), {"org_login": "acme"}
            ).scalar()
            assert got1 is True
            assert got2 is False

            conn1.commit()  # releases conn1's advisory lock

            got2_retry = conn2.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:org_login))"), {"org_login": "acme"}
            ).scalar()
            assert got2_retry is True
        finally:
            conn2.rollback()


def test_sync_org_installation_rejects_live_non_admin(db):
    """An existing org must not bootstrap admin membership for a caller GitHub reports as only a "member"."""
    org_repo.get_or_create(db, github_login="acme")
    me = _make_user(db, "regular@e.com", github_login="regular")
    with (
        _mock_installation("acme", "Organization"),
        patch("src.routers.installations.github_app.get_installation_token", return_value="itok"),
        patch("src.routers.installations.github_app.get_org_membership_role", return_value="member"),
    ):
        resp = _client(db, me).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 403
    org = org_repo.get_by_login(db, "acme")
    assert org_membership_repo.get(db, org.id, me.id) is None


def test_sync_org_installation_requires_installation_id_to_bootstrap(db):
    me = _make_user(db, "founder@e.com", github_login="founder")
    resp = _client(db, me).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "acme", "account_type": "Organization"},
    )
    assert resp.status_code == 404
    assert org_repo.get_by_login(db, "acme") is None


def test_sync_org_installation_rejects_account_login_org_login_mismatch(db, acme_org):
    resp = _client(db, acme_org["admin"]).post(
        "/orgs/acme/installations/sync",
        json={"account_login": "widgets-inc", "account_type": "Organization", "installation_id": 7},
    )
    assert resp.status_code == 403


def test_sync_org_installation_bootstrap_returns_503_when_app_not_configured(db):
    me = _make_user(db, "founder@e.com", github_login="founder")
    with (
        _mock_installation("acme", "Organization"),
        patch(
            "src.routers.installations.github_app.get_installation_token",
            side_effect=github_app.GitHubAppNotConfigured("not configured"),
        ),
    ):
        resp = _client(db, me).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 503
    assert org_repo.get_by_login(db, "acme") is None


def test_sync_org_installation_bootstrap_returns_400_on_other_github_api_error(db):
    me = _make_user(db, "founder@e.com", github_login="founder")
    response = httpx.Response(500, request=httpx.Request("GET", "https://api.github.com/orgs/acme/memberships/founder"))
    with (
        _mock_installation("acme", "Organization"),
        patch(
            "src.routers.installations.github_app.get_installation_token",
            side_effect=httpx.HTTPStatusError("server error", request=response.request, response=response),
        ),
    ):
        resp = _client(db, me).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 400
    assert org_repo.get_by_login(db, "acme") is None


def test_sync_org_installation_bootstrap_returns_503_on_github_network_error(db):
    me = _make_user(db, "founder@e.com", github_login="founder")
    with (
        _mock_installation("acme", "Organization"),
        patch(
            "src.routers.installations.github_app.get_installation_token",
            side_effect=httpx.ConnectError("connection refused"),
        ),
    ):
        resp = _client(db, me).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 503
    assert org_repo.get_by_login(db, "acme") is None


def test_sync_org_installation_requires_admin_unlinked_github_account_no_network_call(db, acme_org):
    """A non-admin with no linked GitHub account gets 403 without any GitHub call."""
    with patch("src.routers.installations.github_app.get_installation_token") as mock_token:
        resp = _client(db, acme_org["member"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 403
    mock_token.assert_not_called()


def test_sync_org_installation_skips_verification_when_installation_id_omitted(db, acme_org):
    with patch("src.routers.installations.github_app.get_installation") as mock_get:
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization"},
        )
    assert resp.status_code == 200
    mock_get.assert_not_called()


def test_sync_personal_installation_rejects_installation_id_owned_by_a_different_account(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with _mock_installation("someone-else", "User"):
        resp = _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
        )
    assert resp.status_code == 422
    assert installation_repo.list_for_user(db, owner_user_id=me.id) == []


def test_sync_personal_installation_rejects_nonexistent_installation_id(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/app/installations/3"))
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=httpx.HTTPStatusError("not found", request=response.request, response=response),
    ):
        resp = _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
        )
    assert resp.status_code == 422
    assert installation_repo.list_for_user(db, owner_user_id=me.id) == []


def test_sync_personal_installation_returns_503_when_app_not_configured(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=github_app.GitHubAppNotConfigured("not configured"),
    ):
        resp = _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User", "installation_id": 3},
        )
    assert resp.status_code == 503
    assert installation_repo.list_for_user(db, owner_user_id=me.id) == []


def test_sync_personal_installation_skips_verification_when_installation_id_omitted(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with patch("src.routers.installations.github_app.get_installation") as mock_get:
        resp = _client(db, me).post(
            "/me/installations/sync",
            json={"account_login": "shabnam", "account_type": "User"},
        )
    assert resp.status_code == 200
    mock_get.assert_not_called()


def test_lookup_installation_returns_account(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with _mock_installation("shabnam", "User"):
        resp = _client(db, me).get("/me/installations/lookup/3")
    assert resp.status_code == 200
    assert resp.json() == {"account_login": "shabnam", "account_type": "User"}


def test_lookup_installation_org_account(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with _mock_installation("acme", "Organization"):
        resp = _client(db, me).get("/me/installations/lookup/7")
    assert resp.status_code == 200
    assert resp.json() == {"account_login": "acme", "account_type": "Organization"}


def test_lookup_installation_requires_auth(db):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get("/me/installations/lookup/3")
    assert resp.status_code == 401


def test_lookup_installation_rejects_nonexistent_installation_id(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    response = httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/app/installations/3"))
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=httpx.HTTPStatusError("not found", request=response.request, response=response),
    ):
        resp = _client(db, me).get("/me/installations/lookup/3")
    assert resp.status_code == 422


def test_lookup_installation_returns_503_when_app_not_configured(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    with patch(
        "src.routers.installations.github_app.get_installation",
        side_effect=github_app.GitHubAppNotConfigured("not configured"),
    ):
        resp = _client(db, me).get("/me/installations/lookup/3")
    assert resp.status_code == 503


def test_delete_org_installation_admin_disconnects(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org["org"].id
    )
    with patch("src.routers.installations.github_app.delete_installation") as mock_delete:
        resp = _client(db, acme_org["admin"]).delete("/orgs/acme/installations/42")

    assert resp.status_code == 204
    mock_delete.assert_called_once_with(42)
    assert installation_repo.get_by_installation_id_for_org(db, org_id=acme_org["org"].id, installation_id=42) is None
    logs = db.query(AuditLog).filter(AuditLog.action == "installation.disconnected").all()
    assert len(logs) == 1
    assert logs[0].actor == acme_org["admin"].email


def test_delete_org_installation_member_forbidden(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org["org"].id
    )
    resp = _client(db, acme_org["member"]).delete("/orgs/acme/installations/42")
    assert resp.status_code == 403
    assert installation_repo.get_by_installation_id_for_org(db, org_id=acme_org["org"].id, installation_id=42) is not None


def test_delete_org_installation_outsider_forbidden(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org["org"].id
    )
    resp = _client(db, _OUTSIDER).delete("/orgs/acme/installations/42")
    assert resp.status_code == 403


def test_delete_org_installation_nonexistent_installation_id_404s(db, acme_org):
    resp = _client(db, acme_org["admin"]).delete("/orgs/acme/installations/999")
    assert resp.status_code == 404


def test_delete_org_installation_cannot_delete_another_orgs_installation(db, acme_org):
    """An acme admin can't disconnect another org's installation by naming its installation_id."""
    other_admin = _make_user(db, "other-admin@e.com")
    other_org = org_repo.get_or_create(db, github_login="other-org")
    org_membership_repo.get_or_create(db, org_id=other_org.id, user_id=other_admin.id, role="admin")
    installation_repo.create(
        db, account_login="other-org", account_type="Organization", auth_mode="app", installation_id=42, org_id=other_org.id
    )
    resp = _client(db, acme_org["admin"]).delete("/orgs/acme/installations/42")
    assert resp.status_code == 404

    # Verify via a request as other-org's admin: acme's RLS context would hide the row in a
    # same-session lookup and pass for the wrong reason.
    list_resp = _client(db, other_admin).get("/orgs/other-org/installations")
    assert list_resp.status_code == 200
    assert any(i["installation_id"] == 42 for i in list_resp.json())


def test_delete_org_installation_github_error_leaves_row_intact(db, acme_org):
    """If GitHub's uninstall fails (not 404), the local row must survive so a retry works."""
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org["org"].id
    )
    response = httpx.Response(500, request=httpx.Request("DELETE", "https://api.github.com/app/installations/42"))
    with patch(
        "src.routers.installations.github_app.delete_installation",
        side_effect=httpx.HTTPStatusError("server error", request=response.request, response=response),
    ):
        resp = _client(db, acme_org["admin"]).delete("/orgs/acme/installations/42")

    assert resp.status_code == 400
    assert installation_repo.get_by_installation_id_for_org(db, org_id=acme_org["org"].id, installation_id=42) is not None


def test_delete_org_installation_returns_503_when_app_not_configured(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org["org"].id
    )
    with patch(
        "src.routers.installations.github_app.delete_installation",
        side_effect=github_app.GitHubAppNotConfigured("not configured"),
    ):
        resp = _client(db, acme_org["admin"]).delete("/orgs/acme/installations/42")
    assert resp.status_code == 503
    assert installation_repo.get_by_installation_id_for_org(db, org_id=acme_org["org"].id, installation_id=42) is not None


def test_delete_personal_installation_owner_disconnects(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=7, owner_user_id=me.id
    )
    with patch("src.routers.installations.github_app.delete_installation") as mock_delete:
        resp = _client(db, me).delete("/me/installations/7")

    assert resp.status_code == 204
    mock_delete.assert_called_once_with(7)
    assert installation_repo.get_by_installation_id_for_user(db, owner_user_id=me.id, installation_id=7) is None
    logs = db.query(AuditLog).filter(AuditLog.action == "installation.disconnected.personal").all()
    assert len(logs) == 1


def test_delete_personal_installation_cannot_delete_another_users_installation(db):
    me = _make_user(db, "shabnam@e.com", github_login="shabnam")
    other = _make_user(db, "other@e.com", github_login="other")
    installation_repo.create(
        db, account_login="other", account_type="User", auth_mode="app", installation_id=7, owner_user_id=other.id
    )
    resp = _client(db, me).delete("/me/installations/7")
    assert resp.status_code == 404

    # Verify via a request as `other`: `me`'s RLS context would hide the row in a same-session
    # lookup and pass for the wrong reason.
    list_resp = _client(db, other).get("/me/installations")
    assert list_resp.status_code == 200
    assert any(i["installation_id"] == 7 for i in list_resp.json())


def test_delete_personal_installation_requires_auth(db):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).delete("/me/installations/7")
    assert resp.status_code == 401


def test_delete_org_installation_returns_503_when_github_unreachable(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=acme_org["org"].id
    )
    with patch(
        "src.routers.installations.github_app.delete_installation",
        side_effect=httpx.RequestError("connection failed"),
    ):
        resp = _client(db, acme_org["admin"]).delete("/orgs/acme/installations/42")
    assert resp.status_code == 503
    assert installation_repo.get_by_installation_id_for_org(db, org_id=acme_org["org"].id, installation_id=42) is not None


def test_sync_org_installation_captures_granted_permissions(db, acme_org):
    with patch(
        "src.routers.installations.github_app.get_installation",
        return_value={
            "account": {"login": "acme", "type": "Organization"},
            "permissions": {"issues": "write", "contents": "read", "metadata": "read"},
        },
    ):
        resp = _client(db, acme_org["admin"]).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 7},
        )
    assert resp.status_code == 200
    row = installation_repo.list_for_org(db, org_id=acme_org["org"].id)[0]
    assert row.granted_permissions == {"issues": "write", "contents": "read", "metadata": "read"}
    assert row.permissions_synced_at is not None


def test_list_org_installations_reports_blocked_features(db, acme_org):
    row = installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app",
        installation_id=42, org_id=acme_org["org"].id,
    )
    installation_repo.update_permissions(
        db, installation_id=42, permissions={"pull_requests": "write", "metadata": "read"}
    )

    resp = _client(db, acme_org["admin"]).get("/orgs/acme/installations")
    assert resp.status_code == 200
    data = resp.json()[0]
    assert data["permissions_synced_at"] is not None
    blocked = {b["feature"] for b in data["blocked_features"]}
    # pull_requests: write is granted -> stale_pr_nudges not blocked; others still are.
    assert "stale_pr_nudges" not in blocked
    assert "bulk_branch_protection" in blocked


def test_list_installations_never_permission_checked_has_empty_blocked_features(db, acme_org):
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app",
        installation_id=42, org_id=acme_org["org"].id,
    )
    resp = _client(db, acme_org["admin"]).get("/orgs/acme/installations")
    data = resp.json()[0]
    assert data["permissions_synced_at"] is None
    assert data["blocked_features"] == []
