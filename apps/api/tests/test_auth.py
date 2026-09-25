"""Tests for auth router and config router."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Query

from src.core.auth import UserOut, require_auth, require_workspace_admin
from src.core.db import User, get_db
from src.core.rate_limit import _account_buckets as _account_rate_limit_buckets
from src.core.rate_limit import _buckets as _rate_limit_buckets
from src.repositories import invitation_repo, org_repo
from src.core.auth import SETUP_LOCK_KEY as _SETUP_LOCK_KEY
from src.routers.auth import _pending_invitations_for
from src.routers.auth import router as auth_router
from src.routers.config import router as config_router


# ── Auth router ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the process-wide per-IP and per-email auth rate-limit buckets between tests."""
    _rate_limit_buckets.clear()
    _account_rate_limit_buckets.clear()
    yield
    _rate_limit_buckets.clear()
    _account_rate_limit_buckets.clear()


@pytest.fixture()
def auth_app(db):
    a = FastAPI()
    a.include_router(auth_router, prefix="/auth")
    a.dependency_overrides[get_db] = lambda: db
    return a


@pytest.fixture()
def auth_client(auth_app):
    return TestClient(auth_app)


def _setup_owner(client, email="owner@example.com", password="supersecret1234"):
    """POST /auth/setup and return the response body."""
    resp = client.post("/auth/setup", json={"email": email, "password": password})
    assert resp.status_code == 201
    return resp.json()


# setup-required

def test_setup_required_no_users(auth_client):
    resp = auth_client.get("/auth/setup-required")
    assert resp.status_code == 200
    assert resp.json()["setup_required"] is True


def test_setup_required_with_user(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.get("/auth/setup-required")
    assert resp.status_code == 200
    assert resp.json()["setup_required"] is False


# setup

def test_setup_returns_token_and_owner(auth_client):
    body = _setup_owner(auth_client)
    assert "access_token" in body
    assert body["user"]["is_workspace_admin"] is True
    assert body["user"]["email"] == "owner@example.com"


def test_setup_creates_a_personal_tenant_and_self_membership(auth_client, db):
    from src.core.db import Membership, Tenant

    body = _setup_owner(auth_client)
    user_id = body["user"]["id"]

    tenant = db.query(Tenant).filter(Tenant.kind == "personal", Tenant.personal_user_id == user_id).first()
    assert tenant is not None
    membership = db.query(Membership).filter(Membership.tenant_id == tenant.id, Membership.user_id == user_id).first()
    assert membership is not None
    assert membership.role == "admin"


def test_setup_stores_email_lowercased(auth_client):
    resp = auth_client.post(
        "/auth/setup", json={"email": "Owner@Example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == "owner@example.com"


def test_setup_rejects_short_password(auth_client):
    resp = auth_client.post("/auth/setup", json={"email": "a@b.com", "password": "tooshort"})
    assert resp.status_code == 422


def test_setup_rejects_password_over_72_bytes(auth_client):
    # bcrypt silently truncates past 72 bytes, so an oversized password must be a clean 422.
    resp = auth_client.post("/auth/setup", json={"email": "a@b.com", "password": "x" * 73})
    assert resp.status_code == 422


def test_setup_rejects_duplicate(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/setup", json={"email": "other@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


def test_setup_applies_a_per_ip_rate_limit(auth_client):
    # /auth/setup is rate-limited like its sibling auth endpoints.
    _setup_owner(auth_client)  # call 1 (201)
    for _ in range(9):  # calls 2-10 (409, setup already complete) -- still count toward the limit
        auth_client.post(
            "/auth/setup", json={"email": "other@example.com", "password": "supersecret1234"}
        )
    resp = auth_client.post(  # call 11 -- exceeds the default max_requests=10
        "/auth/setup", json={"email": "another@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 429


def test_setup_advisory_lock_serializes_concurrent_holders(_engine):
    # Concurrent setups with different emails both pass count()==0 and no unique constraint
    # catches it, so only the advisory lock can: a second connection must block until released.
    with _engine.connect() as conn1, _engine.connect() as conn2:
        conn1.begin()
        conn2.begin()
        try:
            got1 = conn1.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY}
            ).scalar()
            got2 = conn2.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY}
            ).scalar()
            assert got1 is True
            assert got2 is False

            conn1.commit()  # releases conn1's advisory lock

            got2_retry = conn2.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY}
            ).scalar()
            assert got2_retry is True
        finally:
            conn2.rollback()


def test_setup_concurrent_duplicate_email_returns_409_not_500(auth_client, db):
    # Backstop behind the advisory lock for same-email racers: fake count() to a miss so the
    # insert hits the users.email unique constraint and returns 409, not 500.
    _setup_owner(auth_client, email="owner@example.com")
    from src.core.db import User

    def racy_count(self):
        return 0

    with patch.object(Query, "count", racy_count):
        resp = auth_client.post(
            "/auth/setup", json={"email": "owner@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 409
    assert db.query(User).filter(User.email == "owner@example.com").count() == 1


# register

def test_register_creates_non_owner(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["is_workspace_admin"] is False
    assert body["user"]["email"] == "member@example.com"


def test_register_creates_a_personal_tenant_and_self_membership(auth_client, db):
    from src.core.db import Membership, Tenant

    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201
    user_id = resp.json()["user"]["id"]

    tenant = db.query(Tenant).filter(Tenant.kind == "personal", Tenant.personal_user_id == user_id).first()
    assert tenant is not None
    membership = db.query(Membership).filter(Membership.tenant_id == tenant.id, Membership.user_id == user_id).first()
    assert membership is not None
    assert membership.role == "admin"


def test_register_before_setup_rejected(auth_client):
    """/auth/setup must run first to create the workspace admin — registering before that
    would leave the instance with no admin, since /setup 409s once any user exists."""
    resp = auth_client.post(
        "/auth/register", json={"email": "first@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


def test_register_rejects_short_password(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post("/auth/register", json={"email": "a@b.com", "password": "tooshort"})
    assert resp.status_code == 422


def test_register_rejects_password_over_72_bytes(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post("/auth/register", json={"email": "a@b.com", "password": "x" * 73})
    assert resp.status_code == 422


def test_register_rejects_duplicate_email(auth_client):
    _setup_owner(auth_client, email="dupe@example.com")
    resp = auth_client.post(
        "/auth/register", json={"email": "dupe@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


def test_register_concurrent_same_email_returns_409_not_500(auth_client, db):
    # Simulates a same-email register race: the winner's row is committed and the existence
    # check faked to a miss, so the insert hits the unique constraint and must 409, not 500.
    _setup_owner(auth_client, email="owner@example.com")
    from src.core.db import User

    db.add(User(email="race@example.com", name=None, password_hash="x", is_workspace_admin=False))
    db.commit()

    def racy_first(self):
        return None

    with patch.object(Query, "first", racy_first):
        resp = auth_client.post(
            "/auth/register", json={"email": "race@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 409
    assert db.query(User).filter(User.email == "race@example.com").count() == 1


def test_register_rejects_duplicate_email_different_case(auth_client):
    # Emails are case-insensitive: differently-cased duplicates must not both register.
    _setup_owner(auth_client, email="dupe@example.com")
    resp = auth_client.post(
        "/auth/register", json={"email": "Dupe@Example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 409


def test_register_stores_email_lowercased(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/register", json={"email": "MixedCase@Example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201
    assert resp.json()["user"]["email"] == "mixedcase@example.com"


def test_register_disabled_returns_403(auth_client):
    _setup_owner(auth_client)
    with patch("src.routers.auth.get_config", return_value="false"):
        resp = auth_client.post(
            "/auth/register", json={"email": "blocked@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 403


# email verification

def test_setup_creates_a_verified_admin(auth_client, db):
    _setup_owner(auth_client, email="owner@example.com")
    user = db.query(User).filter(User.email == "owner@example.com").first()
    assert user.email_verified is True
    assert user.email_verify_token is None


def test_register_creates_an_unverified_user_with_a_pending_token(auth_client, db):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201
    user = db.query(User).filter(User.email == "member@example.com").first()
    assert user.email_verified is False
    assert user.email_verify_token is not None
    assert user.email_verify_token_expires_at is not None


def test_register_still_succeeds_when_smtp_is_not_configured(auth_client):
    # Default test settings have no SMTP configured -- registration must succeed
    # regardless (account creation must never depend on email sending working).
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 201


def test_register_logs_a_warning_but_does_not_fail_when_email_sending_raises(auth_client):
    _setup_owner(auth_client)
    with patch("src.routers.auth.send_verification_email", side_effect=RuntimeError("smtp exploded")):
        resp = auth_client.post(
            "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 201


def test_register_still_succeeds_when_cors_origins_is_misconfigured_empty(auth_client):
    # A misconfigured CORS_ORIGINS=[] may break the verification email, never registration.
    from src.core.config import settings

    _setup_owner(auth_client)
    with patch.object(settings, "cors_origins", []):
        resp = auth_client.post(
            "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 201


def test_verify_email_marks_the_account_verified_and_clears_the_token(auth_client, db):
    _setup_owner(auth_client)
    auth_client.post("/auth/register", json={"email": "member@example.com", "password": "supersecret1234"})
    user = db.query(User).filter(User.email == "member@example.com").first()
    token = user.email_verify_token

    resp = auth_client.post("/auth/verify-email", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    db.refresh(user)
    assert user.email_verified is True
    assert user.email_verify_token is None
    assert user.email_verify_token_expires_at is None


def test_verify_email_rejects_an_unknown_token(auth_client):
    resp = auth_client.post("/auth/verify-email", json={"token": "not-a-real-token"})
    assert resp.status_code == 400


def test_verify_email_rejects_an_expired_token(auth_client, db):
    _setup_owner(auth_client)
    auth_client.post("/auth/register", json={"email": "member@example.com", "password": "supersecret1234"})
    user = db.query(User).filter(User.email == "member@example.com").first()
    user.email_verify_token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    resp = auth_client.post("/auth/verify-email", json={"token": user.email_verify_token})
    assert resp.status_code == 400
    db.refresh(user)
    assert user.email_verified is False


def test_resend_verification_regenerates_the_token(auth_client, db):
    _setup_owner(auth_client)
    register_resp = auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    token = register_resp.json()["access_token"]
    user = db.query(User).filter(User.email == "member@example.com").first()
    original_token = user.email_verify_token

    resp = auth_client.post("/auth/resend-verification", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "already_verified": False}

    db.refresh(user)
    assert user.email_verify_token is not None
    assert user.email_verify_token != original_token


def test_resend_verification_is_a_noop_for_an_already_verified_account(auth_client):
    token = _setup_owner(auth_client, email="owner@example.com")["access_token"]

    resp = auth_client.post("/auth/resend-verification", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "already_verified": True}


def test_resend_verification_requires_auth(auth_client):
    resp = auth_client.post("/auth/resend-verification")
    assert resp.status_code == 401


# login

def test_login_valid(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_valid_with_different_case_email(auth_client):
    # login must match the stored (lowercased) email regardless of submitted case.
    _setup_owner(auth_client, email="owner@example.com")
    resp = auth_client.post(
        "/auth/login", json={"email": "Owner@Example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(auth_client):
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "wrongpassword12"}
    )
    assert resp.status_code == 401


def test_login_rejects_password_over_72_bytes(auth_client):
    # Stored passwords are <=72 bytes, so an oversized guess must 401 rather than match a
    # shared 72-byte prefix via bcrypt truncation.
    _setup_owner(auth_client)
    resp = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "x" * 200}
    )
    assert resp.status_code == 401


def test_login_unknown_email(auth_client):
    resp = auth_client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 401


def test_login_unknown_email_still_pays_the_bcrypt_cost(auth_client):
    # Unknown emails still run one bcrypt check so timing doesn't reveal registration.
    with patch("src.routers.auth.bcrypt.checkpw", wraps=bcrypt.checkpw) as mock_checkpw:
        resp = auth_client.post(
            "/auth/login", json={"email": "nobody@example.com", "password": "supersecret1234"}
        )
    assert resp.status_code == 401
    mock_checkpw.assert_called_once()


def test_login_applies_a_per_account_rate_limit(auth_client):
    with patch("src.routers.auth.check_account_rate_limit") as mock_limit:
        auth_client.post(
            "/auth/login", json={"email": "Someone@Example.com", "password": "supersecret1234"}
        )
    # Lowercased so "Someone@Example.com" and "someone@example.com" share a bucket.
    mock_limit.assert_called_once_with("login:someone@example.com")


def test_login_github_only_user_returns_401(auth_client, db):
    from src.core.db import User

    db.add(User(email="github-only@example.com", password_hash=None, is_workspace_admin=False))
    db.commit()
    resp = auth_client.post(
        "/auth/login", json={"email": "github-only@example.com", "password": "anypassword12"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


# pending invitations surfaced at register/login

def test_register_never_surfaces_pending_invitation(auth_client, db):
    """A just-registered email isn't proof of inbox control, so the register response must
    never expose a matching pending invitation to someone who merely knows the email."""
    owner = _setup_owner(auth_client, email="owner@example.com")
    org = org_repo.get_or_create(db, github_login="acme")
    invitation_repo.create(db, org_id=org.id, email="newmember@example.com", invited_by_user_id=owner["user"]["id"])

    # Sanity check: the invitation genuinely exists and matches — this isn't a case
    # of "there was nothing to leak".
    assert len(invitation_repo.list_pending_for_email(db, "newmember@example.com")) == 1

    resp = auth_client.post(
        "/auth/register", json={"email": "newmember@example.com", "password": "supersecret1234"}
    )

    assert resp.status_code == 201
    assert resp.json()["pending_invitations"] == []


def test_login_surfaces_pending_invitation(auth_client, db):
    owner = _setup_owner(auth_client, email="owner@example.com")
    auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    org = org_repo.get_or_create(db, github_login="acme")
    invitation_repo.create(db, org_id=org.id, email="member@example.com", invited_by_user_id=owner["user"]["id"])

    resp = auth_client.post(
        "/auth/login", json={"email": "member@example.com", "password": "supersecret1234"}
    )

    assert resp.status_code == 200
    pending = resp.json()["pending_invitations"]
    assert len(pending) == 1
    assert pending[0]["org_login"] == "acme"


def test_pending_invitations_for_batches_org_lookup_across_multiple_orgs():
    """_pending_invitations_for's batched org lookup still maps each invitation to its own org.

    Uses a mocked Session: invitations' RLS matches a single app.tenant_id, so a live query
    under CI's clevis_api role can't see two orgs' rows at once."""
    acme = SimpleNamespace(id=1, github_login="acme")
    globex = SimpleNamespace(id=2, github_login="globex")
    now = datetime.now(timezone.utc)
    inv_acme = SimpleNamespace(org_id=1, expires_at=now)
    inv_globex = SimpleNamespace(org_id=2, expires_at=now)

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = [acme, globex]

    with patch("src.routers.auth.invitation_repo.list_pending_for_email", return_value=[inv_acme, inv_globex]):
        summaries = _pending_invitations_for(fake_db, "member@example.com")

    assert {s.org_login for s in summaries} == {"acme", "globex"}
    fake_db.query.assert_called_once()


def test_login_omits_expired_invitation(auth_client, db):
    owner = _setup_owner(auth_client, email="owner@example.com")
    auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    org = org_repo.get_or_create(db, github_login="acme")
    invitation = invitation_repo.create(
        db, org_id=org.id, email="member@example.com", invited_by_user_id=owner["user"]["id"]
    )
    invitation.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    resp = auth_client.post(
        "/auth/login", json={"email": "member@example.com", "password": "supersecret1234"}
    )

    assert resp.status_code == 200
    assert resp.json()["pending_invitations"] == []


def test_login_omits_accepted_invitation(auth_client, db):
    owner = _setup_owner(auth_client, email="owner@example.com")
    auth_client.post(
        "/auth/register", json={"email": "member@example.com", "password": "supersecret1234"}
    )
    org = org_repo.get_or_create(db, github_login="acme")
    invitation = invitation_repo.create(
        db, org_id=org.id, email="member@example.com", invited_by_user_id=owner["user"]["id"]
    )
    invitation.status = "accepted"
    db.commit()

    resp = auth_client.post(
        "/auth/login", json={"email": "member@example.com", "password": "supersecret1234"}
    )

    assert resp.status_code == 200
    assert resp.json()["pending_invitations"] == []


def test_login_with_no_pending_invitations_returns_empty_list(auth_client):
    _setup_owner(auth_client, email="owner@example.com")
    resp = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    )
    assert resp.status_code == 200
    assert resp.json()["pending_invitations"] == []


# me

def test_me_unauthenticated(auth_client):
    resp = auth_client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_profile(auth_client):
    token = _setup_owner(auth_client)["access_token"]
    resp = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "owner@example.com"
    assert data["is_workspace_admin"] is True


def test_patch_me_unauthenticated(auth_client):
    resp = auth_client.patch("/auth/me", json={"name": "Alice"})
    assert resp.status_code == 401


def test_patch_me_updates_name(auth_client):
    token = _setup_owner(auth_client)["access_token"]
    resp = auth_client.patch(
        "/auth/me",
        json={"name": "Alice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Alice"


# revoke-sessions

def test_revoke_sessions_unauthenticated(auth_client):
    resp = auth_client.post("/auth/me/revoke-sessions")
    assert resp.status_code == 401


def test_revoke_sessions_invalidates_existing_token(auth_client):
    token = _setup_owner(auth_client)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = auth_client.post("/auth/me/revoke-sessions", headers=headers)
    assert resp.status_code == 200

    resp = auth_client.get("/auth/me", headers=headers)
    assert resp.status_code == 401


def test_revoke_sessions_new_login_still_works(auth_client):
    _setup_owner(auth_client)
    auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    )
    first_token = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    ).json()["access_token"]
    auth_client.post(
        "/auth/me/revoke-sessions", headers={"Authorization": f"Bearer {first_token}"}
    )
    new_token = auth_client.post(
        "/auth/login", json={"email": "owner@example.com", "password": "supersecret1234"}
    ).json()["access_token"]
    resp = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert resp.status_code == 200


# ── Config router ─────────────────────────────────────────────────────────────

_OWNER = UserOut(id=1, email="owner@example.com", name=None, is_workspace_admin=True)
_VIEWER = UserOut(id=2, email="viewer@example.com", name=None, is_workspace_admin=False)

_MOCK_CONFIG = {
    "worker_poll_seconds": "5",
}


@pytest.fixture()
def config_client():
    """No auth override — all protected endpoints return 401."""
    a = FastAPI()
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


@pytest.fixture()
def config_client_viewer():
    """Authenticated as a non-owner user."""
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: _VIEWER
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


@pytest.fixture()
def config_owner(db):
    u = User(email="cfg-owner@example.com", password_hash=None, is_workspace_admin=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return UserOut(id=u.id, email=u.email, name=None, is_workspace_admin=True)


@pytest.fixture()
def config_client_owner(db, config_owner):
    """Authenticated as the owner (a real user row, so the update's audit write can land)."""
    a = FastAPI()
    a.dependency_overrides[require_auth] = lambda: config_owner
    a.dependency_overrides[require_workspace_admin] = lambda: config_owner
    a.dependency_overrides[get_db] = lambda: db
    a.include_router(config_router, prefix="/config")
    return TestClient(a)


def test_update_config_is_audited(config_client_owner, db):
    from src.core.db import AuditLog

    with patch("src.routers.config.set_config"), patch("src.routers.config.read_all", return_value={}):
        resp = config_client_owner.put("/config/registration_enabled", json={"value": "false"})
    assert resp.status_code == 200
    row = db.query(AuditLog).filter(AuditLog.action == "config.update").one()
    assert row.target == "registration_enabled" and row.actor == "cfg-owner@example.com"


def test_get_config_unauthenticated(config_client):
    resp = config_client.get("/config")
    assert resp.status_code == 401


def test_get_config_non_owner_forbidden(config_client_viewer):
    resp = config_client_viewer.get("/config")
    assert resp.status_code == 403


def test_get_config_owner(config_client_owner):
    with patch("src.routers.config.read_all", return_value=_MOCK_CONFIG):
        resp = config_client_owner.get("/config")
    assert resp.status_code == 200
    assert resp.json()["worker_poll_seconds"] == "5"


def test_update_config_unauthenticated(config_client):
    resp = config_client.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 401


def test_update_config_non_owner_forbidden(config_client_viewer):
    resp = config_client_viewer.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 403


def test_update_config_unknown_key(config_client_owner):
    resp = config_client_owner.put("/config/unknown_key", json={"value": "x"})
    assert resp.status_code == 400


def test_update_config_invalid_int(config_client_owner):
    resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "notanint"})
    assert resp.status_code == 422


@pytest.mark.parametrize("value", ["0", "-5"])
def test_update_config_int_below_minimum(config_client_owner, value):
    resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": value})
    assert resp.status_code == 422


def test_update_config_valid_worker_poll_seconds(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value=_MOCK_CONFIG),
    ):
        resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("worker_poll_seconds", "10", db=ANY)


# github_api_base and cors_origins are env-only, not runtime-editable.
@pytest.mark.parametrize("key", ["github_api_base", "cors_origins"])
def test_update_config_removed_keys_rejected(config_client_owner, key):
    resp = config_client_owner.put(f"/config/{key}", json={"value": "https://x.com"})
    assert resp.status_code == 400


def test_update_config_digest_cadence_rejects_bad_value(config_client_owner):
    resp = config_client_owner.put("/config/digest_cadence", json={"value": "daily"})
    assert resp.status_code == 422
    assert "one of" in resp.json()["detail"]


@pytest.mark.parametrize("value", ["off", "weekly", "monthly"])
def test_update_config_digest_cadence_accepts_valid_values(config_client_owner, value):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value=_MOCK_CONFIG),
    ):
        resp = config_client_owner.put("/config/digest_cadence", json={"value": value})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("digest_cadence", value, db=ANY)


@pytest.mark.parametrize("value", ["yes", "1", ""])
def test_update_config_invalid_bool(config_client_owner, value):
    resp = config_client_owner.put("/config/registration_enabled", json={"value": value})
    assert resp.status_code == 422


def test_update_config_valid_bool(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value={**_MOCK_CONFIG, "registration_enabled": "false"}),
    ):
        resp = config_client_owner.put("/config/registration_enabled", json={"value": "false"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("registration_enabled", "false", db=ANY)


@pytest.mark.parametrize("value", ["weekly", "yes", ""])
def test_update_config_invalid_pr_nudge_mode(config_client_owner, value):
    resp = config_client_owner.put("/config/pr_nudge_mode", json={"value": value})
    assert resp.status_code == 422


@pytest.mark.parametrize("value", ["off", "comment", "label"])
def test_update_config_valid_pr_nudge_mode(config_client_owner, value):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value={**_MOCK_CONFIG, "pr_nudge_mode": value}),
    ):
        resp = config_client_owner.put("/config/pr_nudge_mode", json={"value": value})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("pr_nudge_mode", value, db=ANY)


def test_update_config_pr_nudge_stale_days_is_int_validated(config_client_owner):
    resp = config_client_owner.put("/config/pr_nudge_stale_days", json={"value": "0"})
    assert resp.status_code == 422


@pytest.mark.parametrize("key", ["membership_reconcile_poll_seconds", "membership_reconcile_stale_hours"])
def test_update_config_membership_reconcile_keys_are_int_validated(config_client_owner, key):
    # These keys must be int-validated so a non-numeric value 422s at write time instead of
    # silently falling back to the default on every loop iteration.
    resp = config_client_owner.put(f"/config/{key}", json={"value": "notanint"})
    assert resp.status_code == 422


def test_update_config_success(config_client_owner):
    with (
        patch("src.routers.config.set_config") as mock_set,
        patch("src.routers.config.read_all", return_value={**_MOCK_CONFIG, "worker_poll_seconds": "10"}),
    ):
        resp = config_client_owner.put("/config/worker_poll_seconds", json={"value": "10"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with("worker_poll_seconds", "10", db=ANY)
    assert resp.json()["worker_poll_seconds"] == "10"


def test_require_auth_trusts_db_admin_flag_over_token_claim(db):
    from fastapi.security import HTTPAuthorizationCredentials

    from src.core.auth import create_access_token, require_auth

    user = User(email="plain@example.com", password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    forged_claims = create_access_token(user.id, "other@example.com", True, None, user.token_version)

    out = require_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=forged_claims), None, db)

    assert out.is_workspace_admin is False
    assert out.email == "plain@example.com"
