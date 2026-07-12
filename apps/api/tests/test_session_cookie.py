"""Cookie-based session auth: require_auth accepts the session cookie; set/clear helpers."""

import pytest
from fastapi import HTTPException, Response

from src.core.auth import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    create_access_token,
    require_auth,
    set_session_cookie,
)
from src.core.db import User


def _make_user(db, email: str, is_workspace_admin: bool = False, name: str | None = None) -> User:
    user = User(email=email, name=name, password_hash=None, is_workspace_admin=is_workspace_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_require_auth_accepts_session_cookie(db):
    user = _make_user(db, "a@b.com", name="A")
    token = create_access_token(user.id, user.email, is_workspace_admin=False, name="A", token_version=user.token_version)
    result = require_auth(credentials=None, session=token, db=db)
    assert result.id == user.id
    assert result.email == "a@b.com"
    assert result.is_workspace_admin is False


def test_require_auth_header_takes_precedence_over_cookie(db):
    from fastapi.security import HTTPAuthorizationCredentials

    header_user = _make_user(db, "header@b.com", is_workspace_admin=True)
    cookie_user = _make_user(db, "cookie@b.com")
    header_token = create_access_token(
        header_user.id, header_user.email, is_workspace_admin=True, name=None, token_version=header_user.token_version
    )
    cookie_token = create_access_token(
        cookie_user.id, cookie_user.email, is_workspace_admin=False, name=None, token_version=cookie_user.token_version
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=header_token)
    result = require_auth(credentials=creds, session=cookie_token, db=db)
    assert result.id == header_user.id
    assert result.is_workspace_admin is True


def test_require_auth_rejects_when_missing(db):
    with pytest.raises(HTTPException) as exc:
        require_auth(credentials=None, session=None, db=db)
    assert exc.value.status_code == 401


def test_require_auth_rejects_revoked_session(db):
    user = _make_user(db, "revoked@b.com")
    token = create_access_token(user.id, user.email, is_workspace_admin=False, name=None, token_version=user.token_version)
    user.token_version += 1
    db.commit()
    with pytest.raises(HTTPException) as exc:
        require_auth(credentials=None, session=token, db=db)
    assert exc.value.status_code == 401


def test_set_session_cookie_is_httponly():
    resp = Response()
    set_session_cookie(resp, "jwt-value")
    header = resp.headers["set-cookie"]
    assert f"{SESSION_COOKIE_NAME}=jwt-value" in header
    assert "HttpOnly" in header
    assert "Path=/" in header


def test_clear_session_cookie():
    resp = Response()
    clear_session_cookie(resp)
    header = resp.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in header
    # delete_cookie expires the cookie immediately
    assert ("Max-Age=0" in header) or ("expires=" in header.lower())


def test_logout_endpoint_clears_cookie():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router, prefix="/auth")
    resp = TestClient(app).post("/auth/logout")
    assert resp.status_code == 200
    assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", "")
