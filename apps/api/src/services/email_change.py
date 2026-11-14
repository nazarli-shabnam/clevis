"""Stateless, single-use tokens that confirm a change of account email.

Same shape as the password-reset token (see ``password_reset``): signed with a key derived from ``AUTH_SECRET``
that is different from the session key, so it can never authenticate a request. It carries the user id, the new
address, an expiry, and a fingerprint of the *current* address; once the change is applied the fingerprint no
longer matches, which makes the link single-use without storing anything. The fingerprint also covers the password hash and session version, so a password change or reset, or signing out everywhere, cancels a pending change.
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


def _fingerprint(user) -> str:
    """The account's email and credential state: a change of address, a new password (change or reset) or a
    sign-out-everywhere all alter it, so any of them cancels a pending change."""
    state = f"{user.email.lower()}|{user.password_hash}|{user.token_version}"
    return hashlib.sha256(state.encode()).hexdigest()[:32]


def make_token(user, new_email: str) -> str:
    payload = {
        "typ": _TYPE,
        "sub": str(user.id),
        "new": new_email.lower(),
        "ef": _fingerprint(user),
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


def matches_current_state(fingerprint: str, user) -> bool:
    """False once the address changed (the token was used) or the credentials did."""
    return hmac.compare_digest(fingerprint, _fingerprint(user))
