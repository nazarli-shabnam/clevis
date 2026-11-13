"""Stateless, single-use tokens that confirm a change of account email.

Same shape as the password-reset token (see ``password_reset``): signed with a key derived from ``AUTH_SECRET``
that is different from the session key, so it can never authenticate a request. It carries the user id, the new
address, an expiry, and a fingerprint of the *current* address; once the change is applied the fingerprint no
longer matches, which makes the link single-use without storing anything.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt

from src.core.config import settings

TOKEN_TTL = timedelta(hours=1)
_ALGORITHM = "HS256"
_TYPE = "email-change"


def _key() -> bytes:
    return hashlib.sha256(b"clevis-email-change:" + settings.auth_secret.get_secret_value().encode()).digest()


def _fingerprint(current_email: str) -> str:
    return hashlib.sha256(current_email.lower().encode()).hexdigest()[:32]


def make_token(user_id: int, current_email: str, new_email: str) -> str:
    payload = {
        "typ": _TYPE,
        "sub": str(user_id),
        "new": new_email.lower(),
        "ef": _fingerprint(current_email),
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, _key(), algorithm=_ALGORITHM)


def parse_token(token: str) -> tuple[int, str, str] | None:
    """(user id, new email, current-email fingerprint) of a well-formed, unexpired token, else None."""
    try:
        payload = jwt.decode(token, _key(), algorithms=[_ALGORITHM])
        if payload.get("typ") != _TYPE:
            return None
        return int(payload["sub"]), str(payload["new"]), str(payload["ef"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def matches_current_email(fingerprint: str, current_email: str) -> bool:
    """False once the address has changed (the token was used)."""
    return hmac.compare_digest(fingerprint, _fingerprint(current_email))
