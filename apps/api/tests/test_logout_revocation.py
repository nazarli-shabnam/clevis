"""POST /auth/logout revokes the presented session JWT server-side (#498).

Before, logout only cleared the cookie, so a copied bearer token stayed valid for its whole 30-day
life. Session JWTs now carry a ``jti`` that logout denylists until the token's own ``exp``.
"""
from datetime import datetime, timedelta, timezone

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
    _buckets.clear()
    _account_buckets.clear()
    yield
    _buckets.clear()
    _account_buckets.clear()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture()
def owner_token(client):
    resp = client.post("/auth/setup", json={"email": "owner@example.com", "password": PASSWORD})
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _login(client) -> str:
    resp = client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _claims(token: str) -> dict:
    return jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=["HS256"])


def _encode(**claims) -> str:
    return jwt.encode(claims, settings.auth_secret.get_secret_value(), algorithm="HS256")


def test_every_issued_token_carries_a_unique_jti():
    first = create_access_token(1, "a@example.com", False)
    second = create_access_token(1, "a@example.com", False)

    assert _claims(first)["jti"] and _claims(first)["jti"] != _claims(second)["jti"]


def test_a_logged_out_bearer_token_stops_working(client, owner_token):
    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 200

    resp = client.post("/auth/logout", headers=_bearer(owner_token))

    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")
    again = client.get("/auth/me", headers=_bearer(owner_token))
    assert again.status_code == 401 and again.json()["detail"] == "Session revoked"


def test_a_logged_out_cookie_session_stops_working(client, owner_token):
    cookie = {"Cookie": f"{SESSION_COOKIE_NAME}={owner_token}"}
    assert client.get("/auth/me", headers=cookie).status_code == 200

    assert client.post("/auth/logout", headers=cookie).status_code == 200

    assert client.get("/auth/me", headers=cookie).status_code == 401


def test_logging_out_one_session_leaves_the_users_other_sessions_alone(client, owner_token):
    other = _login(client)

    client.post("/auth/logout", headers=_bearer(owner_token))

    assert client.get("/auth/me", headers=_bearer(owner_token)).status_code == 401
    assert client.get("/auth/me", headers=_bearer(other)).status_code == 200


def test_logout_is_idempotent(client, owner_token, db):
    for _ in range(2):
        assert client.post("/auth/logout", headers=_bearer(owner_token)).status_code == 200

    assert db.query(RevokedToken).count() == 1


def test_the_denylist_row_lives_until_the_tokens_own_expiry(client, owner_token, db):
    client.post("/auth/logout", headers=_bearer(owner_token))

    row = db.get(RevokedToken, _claims(owner_token)["jti"])
    assert row is not None
    assert row.user_id == db.query(User).one().id
    assert row.expires_at == datetime.fromtimestamp(_claims(owner_token)["exp"], tz=timezone.utc)


def test_a_token_issued_before_jti_existed_keeps_working_and_logout_of_it_is_a_no_op(client, owner_token, db):
    claims = _claims(owner_token)
    legacy = _encode(**{k: v for k, v in claims.items() if k != "jti"})
    assert client.get("/auth/me", headers=_bearer(legacy)).status_code == 200

    resp = client.post("/auth/logout", headers=_bearer(legacy))

    assert resp.status_code == 200
    assert db.query(RevokedToken).count() == 0
    # Nothing to revoke it by: it lives until it expires or /me/revoke-sessions, as before.
    assert client.get("/auth/me", headers=_bearer(legacy)).status_code == 200


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
    resp = client.post("/auth/logout", headers=headers)

    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")
    assert db.query(RevokedToken).count() == 0


def test_logging_out_an_already_expired_token_is_a_no_op(client, owner_token, db):
    claims = _claims(owner_token)
    expired = _encode(**{**claims, "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())})

    resp = client.post("/auth/logout", headers=_bearer(expired))

    assert resp.status_code == 200
    assert db.query(RevokedToken).count() == 0


def test_a_token_for_a_user_that_no_longer_exists_is_a_no_op(client, db):
    ghost = create_access_token(987654, "ghost@example.com", False)

    assert client.post("/auth/logout", headers=_bearer(ghost)).status_code == 200
    assert db.query(RevokedToken).count() == 0


def test_expired_denylist_rows_are_purged_on_the_next_logout(client, owner_token, db):
    user_id = _claims(owner_token)["sub"]
    db.add(RevokedToken(jti="stale", user_id=int(user_id), expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.add(RevokedToken(jti="live", user_id=int(user_id), expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()

    client.post("/auth/logout", headers=_bearer(owner_token))

    assert {r.jti for r in db.query(RevokedToken)} == {"live", _claims(owner_token)["jti"]}


def test_revoke_sessions_still_ends_every_session_including_other_unrevoked_ones(client, owner_token):
    other = _login(client)

    client.post("/auth/me/revoke-sessions", headers=_bearer(owner_token))

    assert client.get("/auth/me", headers=_bearer(other)).status_code == 401
