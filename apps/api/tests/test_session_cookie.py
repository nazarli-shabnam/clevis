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


def test_require_auth_accepts_session_cookie():
    token = create_access_token(1, "a@b.com", is_owner=False, name="A")
    user = require_auth(credentials=None, session=token)
    assert user.id == 1
    assert user.email == "a@b.com"


def test_require_auth_header_takes_precedence_over_cookie():
    from fastapi.security import HTTPAuthorizationCredentials

    header_token = create_access_token(2, "header@b.com", is_owner=True, name=None)
    cookie_token = create_access_token(9, "cookie@b.com", is_owner=False, name=None)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=header_token)
    user = require_auth(credentials=creds, session=cookie_token)
    assert user.id == 2


def test_require_auth_rejects_when_missing():
    with pytest.raises(HTTPException) as exc:
        require_auth(credentials=None, session=None)
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
