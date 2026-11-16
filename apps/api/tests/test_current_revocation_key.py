"""current_revocation_key mirrors require_auth's token choice and gives the denylist key a stream re-checks."""

import hashlib
from datetime import datetime, timedelta, timezone

import jwt
from fastapi.security import HTTPAuthorizationCredentials

from src.core.auth import _ALGORITHM, current_revocation_key
from src.core.config import settings


def _token(**claims) -> str:
    payload = {"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), **claims}
    return jwt.encode(payload, settings.auth_secret.get_secret_value(), algorithm=_ALGORITHM)


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_a_token_with_a_jti_is_keyed_by_it():
    assert current_revocation_key(_bearer(_token(jti="abc")), None) == "abc"


def test_a_token_issued_before_jti_existed_is_keyed_by_its_fingerprint():
    token = _token()
    assert current_revocation_key(_bearer(token), None) == "legacy:" + hashlib.sha256(token.rsplit(".", 1)[0].encode()).hexdigest()


def test_the_bearer_token_wins_over_the_cookie_like_require_auth():
    assert current_revocation_key(_bearer(_token(jti="from-header")), _token(jti="from-cookie")) == "from-header"


def test_the_session_cookie_is_used_without_a_bearer_token():
    assert current_revocation_key(None, _token(jti="from-cookie")) == "from-cookie"


def test_no_token_or_an_unverifiable_one_has_no_key():
    assert current_revocation_key(None, None) is None
    assert current_revocation_key(_bearer("not-a-jwt"), None) is None
    expired = _token(jti="old", exp=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert current_revocation_key(_bearer(expired), None) is None
