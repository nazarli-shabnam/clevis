"""Stateless, single-use password-reset tokens.

A token is a JWT signed with a key derived from ``AUTH_SECRET`` but different from the session-signing key, so
a reset token can never authenticate a request (and a session token can never reset a password). It carries the
user id, an expiry, and a fingerprint of the password hash and email current when it was issued: once the password changes
the fingerprint no longer matches, which is what makes it single-use without storing anything.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt

from src.core.config import settings

TOKEN_TTL = timedelta(hours=1)
_ALGORITHM = "HS256"
_TYPE = "password-reset"


def _key() -> bytes:
    return hashlib.sha256(b"clevis-password-reset:" + settings.auth_secret.get_secret_value().encode()).digest()


def _fingerprint(password_hash: str, email: str) -> str:
    # The address is part of it so an email change also invalidates outstanding links to the old mailbox.
    return hashlib.sha256(f"{email.lower()}|{password_hash}".encode()).hexdigest()[:32]


def make_token(user_id: int, password_hash: str, email: str) -> str:
    payload = {
        "typ": _TYPE,
        "sub": str(user_id),
        "pf": _fingerprint(password_hash, email),
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, _key(), algorithm=_ALGORITHM)


def user_id_from_token(token: str) -> tuple[int, str] | None:
    """(user id, password-hash fingerprint) of a well-formed, unexpired token, else None."""
    try:
        payload = jwt.decode(token, _key(), algorithms=[_ALGORITHM])
        if payload.get("typ") != _TYPE:
            return None
        return int(payload["sub"]), str(payload["pf"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def matches_current_password(fingerprint: str, password_hash: str | None, email: str) -> bool:
    """False once the password or email has changed (the token was used or the mailbox replaced) or the account has no password."""
    return password_hash is not None and hmac.compare_digest(fingerprint, _fingerprint(password_hash, email))
