"""POST /auth/logout revokes the presented session JWT server-side (#498).

Before, logout only cleared the cookie, so a copied bearer token stayed valid for its whole 30-day
life. Session JWTs now carry a ``jti`` that logout denylists until the token's own ``exp``.
"""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from unittest.mock import patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import SESSION_COOKIE_NAME, create_access_token
from src.core.config import settings
from src.core.db import RevokedToken, User, get_db
from src.core.rate_limit import _account_buckets, _buckets
from src.routers.auth import router as auth_router

PASSWORD = "supersecret1234"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Isolate logout tests from request and account rate-limit state."""
    _buckets.clear()
    _account_buckets.clear()
    yield
    _buckets.clear()
    _account_buckets.clear()


@pytest.fixture()
def client(db):
    """Serve auth routes using the transactional test database."""
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture()
def owner_token(client):
    """Create the initial workspace admin and return its session token."""
    resp = client.post("/auth/setup", json={"email": "owner@example.com", "password": PASSWORD})
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _login(client) -> str:
    """Issue another session for the existing workspace admin."""
    resp = client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _bearer(token: str) -> dict:
    """Build the Authorization header for a session token."""
    return {"Authorization": f"Bearer {token}"}


def _claims(token: str) -> dict:
    """Verify and decode a locally issued test token."""
    return jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=["HS256"])


def _encode(**claims) -> str:
    """Sign custom claims with the configured test secret."""
    return jwt.encode(claims, settings.auth_secret.get_secret_value(), algorithm="HS256")


def _assert_session_cookie_deleted(response) -> None:
    """The session cookie is present and already expired (Max-Age=0, or an Expires in the past)."""
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    if "Max-Age=0" in set_cookie:
        return
    expires = next(
        (part.split("=", 1)[1].strip() for part in set_cookie.split(";") if part.strip().lower().startswith("expires=")),
        None,
    )
    assert expires is not None, f"cookie is not being deleted: {set_cookie!r}"
    assert parsedate_to_datetime(expires) < datetime.now(timezone.utc)


def test_every_issued_token_carries_a_unique_jti():
    """Separate logins must have distinct revocation identities."""
    first = create_access_token(1, "a@example.com", False)
    second = create_access_token(1, "a@example.com", False)

    assert _claims(first)["jti"] and _claims(first)["jti"] != _claims(second)["jti"]


def test_a_logged_out_bearer_token_stops_working(client, owner_token):
    """Logout clears the cookie and rejects replay of the presented bearer token."""
    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 200

    resp = client.post("/auth/logout", headers=_bearer(owner_token))

    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")
    again = client.get("/auth/me", headers=_bearer(owner_token))
    assert again.status_code == 401 and again.json()["detail"] == "Session revoked"


def test_a_logged_out_cookie_session_stops_working(client, owner_token):
    """Logout rejects subsequent requests using the presented session cookie."""
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={owner_token}"}
    assert client.get("/auth/me", headers=cookie).status_code == 200

    assert client.post("/auth/logout", headers=cookie).status_code == 200

    assert client.get("/auth/me", headers=cookie).status_code == 401


def test_logging_out_one_session_leaves_the_users_other_sessions_alone(client, owner_token):
    """Revoking one token must preserve other sessions for the same user."""
    other = _login(client)

    client.post("/auth/logout", headers=_bearer(owner_token))

    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 401
    assert client.get("/auth/me", headers=_bearer(other)).status_code == 200


def test_logout_is_idempotent(client, owner_token, db):
    """Repeated logout requests must create only one denylist entry."""
    for _ in range(2):
        assert client.post("/auth/logout", headers=_bearer(owner_token)).status_code == 200

    assert db.query(RevokedToken).count() == 1


def test_the_denylist_row_lives_until_the_tokens_own_expiry(client, owner_token, db):
    """Revocation records retain the token owner and original expiration."""
    client.post("/auth/logout", headers=_bearer(owner_token))

    row = db.get(RevokedToken, _claims(owner_token)["jti"])
    assert row is not None
    assert row.user_id == db.query(User).one().id
    assert row.expires_at == datetime.fromtimestamp(_claims(owner_token)["exp"], tz=timezone.utc)


@pytest.mark.parametrize("transport", ["bearer", "cookie", "both"])
def test_logout_revokes_a_legacy_token_without_ending_other_sessions(client, owner_token, db, transport):
    """Legacy sessions stop authenticating after logout through either supported transport."""
    claims = {k: v for k, v in _claims(owner_token).items() if k != "jti"}
    legacy = _encode(**claims)
    other_legacy = _encode(**{**claims, "exp": claims["exp"] + 60})
    headers = {}
    if transport in ("bearer", "both"):
        headers.update(_bearer(legacy))
    if transport in ("cookie", "both"):
        headers["Cookie"] = f"{SESSION_COOKIE_NAME}={legacy}"
    assert client.get("/auth/me", headers=headers).status_code == 200

    for _ in range(2):
        resp = client.post("/auth/logout", headers=headers)
        assert resp.status_code == 200 and resp.json() == {"ok": True}
        assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")

    row = db.query(RevokedToken).one()
    assert row.user_id == int(claims["sub"])
    assert row.expires_at == datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    again = client.get("/auth/me", headers=headers)
    assert again.status_code == 401 and again.json()["detail"] == "Session revoked"
    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 200
    assert client.get("/auth/me", headers=_bearer(other_legacy)).status_code == 200


def test_legacy_signature_encoding_cannot_bypass_logout(client, owner_token):
    """Equivalent signature encodings must share the same legacy revocation record."""
    claims = {k: v for k, v in _claims(owner_token).items() if k != "jti"}
    legacy = _encode(**claims)
    padded = legacy + "="
    assert client.get("/auth/me", headers=_bearer(padded)).status_code == 200

    assert client.post("/auth/logout", headers=_bearer(padded)).status_code == 200

    assert client.get("/auth/me", headers=_bearer(legacy)).status_code == 401
    assert client.get("/auth/me", headers=_bearer(padded)).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer " + jwt.encode({"sub": "1", "jti": "x", "exp": 9999999999}, "a-different-secret-that-is-at-least-32-bytes-long", algorithm="HS256")},
    ],
    ids=["no-token", "garbage-token", "wrong-signature"],
)
def test_logout_still_succeeds_and_clears_the_cookie_without_a_valid_token(client, db, headers):
    """Missing or invalid credentials clear the cookie without storing a revocation."""
    resp = client.post("/auth/logout", headers=headers)

    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")
    assert db.query(RevokedToken).count() == 0


def test_logging_out_an_already_expired_token_is_a_no_op(client, owner_token, db):
    """Expired tokens need no revocation record and must not fail logout."""
    claims = _claims(owner_token)
    expired = _encode(**{**claims, "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())})

    resp = client.post("/auth/logout", headers=_bearer(expired))

    assert resp.status_code == 200
    assert db.query(RevokedToken).count() == 0


def test_a_token_for_a_user_that_no_longer_exists_is_a_no_op(client, db):
    """Logout for a deleted user must not insert an invalid foreign key."""
    ghost = create_access_token(987654, "ghost@example.com", False)

    assert client.post("/auth/logout", headers=_bearer(ghost)).status_code == 200
    assert db.query(RevokedToken).count() == 0


def test_expired_denylist_rows_are_purged_on_the_next_logout(client, owner_token, db):
    """Logout removes expired records while preserving live revocations."""
    user_id = _claims(owner_token)["sub"]
    db.add(RevokedToken(jti="stale", user_id=int(user_id), expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.add(RevokedToken(jti="live", user_id=int(user_id), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()

    client.post("/auth/logout", headers=_bearer(owner_token))

    assert {r.jti for r in db.query(RevokedToken)} == {"live", _claims(owner_token)["jti"]}


def test_revoke_sessions_still_ends_every_session_including_other_unrevoked_ones(client, owner_token):
    """The account-wide revocation counter still invalidates other sessions."""
    other = _login(client)

    client.post("/auth/me/revoke-sessions", headers=_bearer(owner_token))

    assert client.get("/auth/me", headers=_bearer(other)).status_code == 401


def test_logout_revokes_both_the_bearer_token_and_a_different_session_cookie(client, owner_token):
    """Logout must revoke both credentials when a browser presents two sessions."""
    # e.g. a stale localStorage token alongside the cookie a later GitHub OAuth login set.
    cookie_session = _login(client)
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={cookie_session}"}
    assert client.get("/auth/me", headers=cookie).status_code == 200

    client.post("/auth/logout", headers={**_bearer(owner_token), **cookie})

    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 401
    assert client.get("/auth/me", headers=cookie).status_code == 401


def test_logout_with_the_same_token_in_header_and_cookie_stores_one_row(client, owner_token, db):
    """Duplicate credentials in one request must yield a single denylist row."""
    both = {**_bearer(owner_token), "Cookie": f"{SESSION_COOKIE_NAME}={owner_token}"}

    assert client.post("/auth/logout", headers=both).status_code == 200

    assert db.query(RevokedToken).count() == 1


def test_a_database_failure_still_clears_the_cookie_and_reports_503(client, owner_token, caplog):
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={owner_token}"}

    with patch("src.routers.auth.revoke_presented_tokens", side_effect=RuntimeError("db down")):
        resp = client.post("/auth/logout", headers=cookie)

    assert resp.status_code == 503
    assert "could not be revoked" in resp.json()["detail"]
    _assert_session_cookie_deleted(resp)
    assert "could not revoke" in caplog.text


def test_the_cookie_is_still_cleared_when_the_rollback_after_a_failed_revocation_also_fails(client, owner_token, db):
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={owner_token}"}

    with (
        patch("src.routers.auth.revoke_presented_tokens", side_effect=RuntimeError("db down")),
        patch.object(db, "rollback", side_effect=RuntimeError("connection is gone")),
    ):
        resp = client.post("/auth/logout", headers=cookie)

    assert resp.status_code == 503
    _assert_session_cookie_deleted(resp)


def test_the_cookie_deletion_helper_rejects_a_future_expiry():
    class _Resp:
        headers = {"set-cookie": f"{SESSION_COOKIE_NAME}=; Expires=Wed, 01 Jan 2031 00:00:00 GMT; Path=/"}

    with pytest.raises(AssertionError):
        _assert_session_cookie_deleted(_Resp())


def test_a_failed_revocation_leaves_the_bearer_token_honestly_unrevoked(client, owner_token):
    with patch("src.routers.auth.revoke_presented_tokens", side_effect=RuntimeError("db down")):
        client.post("/auth/logout", headers=_bearer(owner_token))

    # Not revoked, which is why the client is told (503) rather than shown a success.
    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 200
