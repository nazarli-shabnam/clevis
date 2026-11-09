"""Active-session list and per-session revoke (#778, migration 0077)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import create_access_token
from src.core.config import settings
from src.core.db import RevokedToken, UserSession, get_db
from src.core.rate_limit import _account_buckets, _buckets
from src.routers.auth import router as auth_router

PASSWORD = "supersecret1234"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36"
SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"


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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _jti(token):
    return jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=["HS256"])["jti"]


def _login(client, email="owner@example.com", ua=CHROME):
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD}, headers={"User-Agent": ua})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture()
def owner(client):
    resp = client.post(
        "/auth/setup", json={"email": "owner@example.com", "password": PASSWORD}, headers={"User-Agent": CHROME}
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _sessions(client, token):
    resp = client.get("/auth/me/sessions", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_sign_in_records_the_session_with_a_device_hint_and_marks_it_current(client, owner):
    rows = _sessions(client, owner)

    assert len(rows) == 1
    assert rows[0]["id"] == _jti(owner) and rows[0]["current"] is True
    assert rows[0]["user_agent"] == CHROME
    assert rows[0]["ip"]  # whatever the test client reports; the point is that one is stored


def test_each_login_is_its_own_session_and_only_the_callers_is_current(client, owner):
    phone = _login(client, ua=SAFARI)

    from_laptop = {s["id"]: s for s in _sessions(client, owner)}
    assert set(from_laptop) == {_jti(owner), _jti(phone)}
    assert [i for i, s in from_laptop.items() if s["current"]] == [_jti(owner)]
    assert from_laptop[_jti(phone)]["user_agent"] == SAFARI

    from_phone = {s["id"]: s for s in _sessions(client, phone)}
    assert [i for i, s in from_phone.items() if s["current"]] == [_jti(phone)]


def test_revoking_another_session_ends_only_that_one(client, owner):
    phone = _login(client, ua=SAFARI)

    resp = client.delete(f"/auth/me/sessions/{_jti(phone)}", headers=_bearer(owner))

    assert resp.status_code == 204
    assert client.get("/auth/me", headers=_bearer(phone)).status_code == 401  # signed out immediately
    assert client.get("/auth/me", headers=_bearer(owner)).status_code == 200  # this device is untouched
    assert [s["id"] for s in _sessions(client, owner)] == [_jti(owner)]


def test_revoking_writes_to_the_existing_denylist_until_the_token_would_have_expired(client, owner, db):
    phone = _login(client)
    expires = datetime.fromtimestamp(
        jwt.decode(phone, settings.auth_secret.get_secret_value(), algorithms=["HS256"])["exp"], tz=timezone.utc
    )

    client.delete(f"/auth/me/sessions/{_jti(phone)}", headers=_bearer(owner))

    row = db.get(RevokedToken, _jti(phone))
    assert row is not None and abs((row.expires_at - expires).total_seconds()) < 2


def test_the_current_session_cannot_be_revoked_here_and_unknown_ids_are_404(client, owner):
    assert client.delete(f"/auth/me/sessions/{_jti(owner)}", headers=_bearer(owner)).status_code == 400
    assert client.get("/auth/me", headers=_bearer(owner)).status_code == 200
    assert client.delete("/auth/me/sessions/does-not-exist", headers=_bearer(owner)).status_code == 404


def test_a_user_cannot_see_or_revoke_someone_elses_session(client, owner, db):
    # Registration needs registration_enabled; set up a second user directly instead.
    from src.core.db import User

    other = User(email="other@example.com", name=None, password_hash=None, is_workspace_admin=False, email_verified=True)
    db.add(other)
    db.commit()
    db.refresh(other)
    other_token = create_access_token(other.id, other.email, False, None, other.token_version)
    db.add(UserSession(jti=_jti(other_token), user_id=other.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()

    assert _jti(other_token) not in {s["id"] for s in _sessions(client, owner)}
    assert client.delete(f"/auth/me/sessions/{_jti(other_token)}", headers=_bearer(owner)).status_code == 404
    assert db.get(UserSession, _jti(other_token)) is not None
    assert db.get(RevokedToken, _jti(other_token)) is None


def test_logging_out_removes_the_session_from_the_list(client, owner):
    phone = _login(client)
    assert client.post("/auth/logout", headers=_bearer(phone)).status_code == 200

    assert [s["id"] for s in _sessions(client, owner)] == [_jti(owner)]


def test_signing_out_everywhere_clears_the_list(client, owner, db):
    _login(client)
    assert client.post("/auth/me/revoke-sessions", headers=_bearer(owner)).status_code == 200

    assert db.query(UserSession).count() == 0


def test_last_seen_moves_but_at_most_once_a_minute(client, owner, db):
    jti = _jti(owner)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=3)
    db.execute(text("UPDATE user_sessions SET last_seen_at = :t WHERE jti = :j"), {"t": long_ago, "j": jti})
    db.commit()

    client.get("/auth/me", headers=_bearer(owner))
    db.expire_all()
    first = db.get(UserSession, jti).last_seen_at
    assert first > long_ago + timedelta(hours=2)

    client.get("/auth/me", headers=_bearer(owner))
    db.expire_all()
    assert db.get(UserSession, jti).last_seen_at == first  # inside the minute: no new write


def test_expired_sessions_are_not_listed_and_are_purged_at_the_next_sign_in(client, owner, db):
    db.add(UserSession(jti="stale", user_id=db.get(UserSession, _jti(owner)).user_id,
                       expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    db.commit()
    assert "stale" not in {s["id"] for s in _sessions(client, owner)}

    _login(client)

    assert db.get(UserSession, "stale") is None


def test_an_overlong_user_agent_is_truncated(client, owner, db):
    token = _login(client, ua="A" * 1000)
    assert len(db.get(UserSession, _jti(token)).user_agent) == 255


def test_a_failure_to_record_the_session_does_not_fail_sign_in(client, owner):
    with patch("src.core.auth.session_repo.record", side_effect=RuntimeError("db hiccup")):
        token = _login(client)

    assert client.get("/auth/me", headers=_bearer(token)).status_code == 200  # the session itself works
    assert _jti(token) not in {s["id"] for s in _sessions(client, owner)}  # it is just not listed


def test_a_legacy_token_without_a_jti_still_authenticates_and_lists_other_sessions(client, owner, db):
    user_id = db.get(UserSession, _jti(owner)).user_id
    legacy = jwt.encode(
        {"sub": str(user_id), "email": "owner@example.com", "token_version": 0,
         "exp": datetime.now(timezone.utc) + timedelta(days=1)},
        settings.auth_secret.get_secret_value(), algorithm="HS256",
    )

    rows = _sessions(client, legacy)

    assert [s["id"] for s in rows] == [_jti(owner)] and not any(s["current"] for s in rows)


def test_the_list_requires_authentication(client):
    assert client.get("/auth/me/sessions").status_code == 401
    assert client.delete("/auth/me/sessions/x").status_code == 401


def test_registering_records_the_new_accounts_session(client, owner):
    """Registering records the new accounts session."""
    resp = client.post(
        "/auth/register",
        json={"email": "member@example.com", "password": PASSWORD},
        headers={"User-Agent": SAFARI},
    )
    assert resp.status_code == 201

    rows = _sessions(client, resp.json()["access_token"])
    assert len(rows) == 1 and rows[0]["current"] is True and rows[0]["user_agent"] == SAFARI


def test_revoking_the_same_session_twice_is_a_404_not_a_server_error(client, owner):
    """Revoking the same session twice is a 404 not a server error."""
    phone = _login(client, ua=SAFARI)
    url = f"/auth/me/sessions/{_jti(phone)}"

    assert client.delete(url, headers=_bearer(owner)).status_code == 204
    assert client.delete(url, headers=_bearer(owner)).status_code == 404
