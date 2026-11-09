"""JWT helpers and FastAPI auth dependencies (org-scoped roles live in src/core/rbac.py).

require_auth checks token_version (log out everywhere) and the per-token ``jti`` denylist (single-session
logout) against the DB on every request so revoked sessions end immediately.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import RevokedToken, User, get_db, set_session_user
from src.repositories import session_repo

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 30

# Arbitrary key for pg_advisory_xact_lock serializing first-user creation (/auth/setup and
# GitHub OAuth signup), so two concurrent first sign-ins can't both become workspace admin.
SETUP_LOCK_KEY = 727100

SESSION_COOKIE_NAME = "clevis_session"
_COOKIE_MAX_AGE_SECONDS = _TOKEN_EXPIRE_DAYS * 24 * 60 * 60

# HTTPBearer, not OAuth2PasswordBearer: /auth/login isn't a form-encoded OAuth2 flow.
_http_bearer = HTTPBearer(auto_error=False)


def set_session_cookie(response: Response, token: str) -> None:
    """Attach the session JWT as an httpOnly cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        domain=settings.session_cookie_domain,
        path="/",
    )


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    is_workspace_admin: bool
    # Set via GitHub OAuth; read fresh from the DB in require_auth, not from the JWT.
    github_login: str | None = None


def create_access_token(
    user_id: int, email: str, is_workspace_admin: bool, name: str | None = None, token_version: int = 0
) -> str:
    """Issue a session JWT with a unique identity for individual logout revocation."""
    payload = {
        "sub": str(user_id),
        "email": email,
        "is_workspace_admin": is_workspace_admin,
        "name": name,
        "token_version": token_version,
        # Unique per token so logout can revoke this one session without touching the user's others.
        "jti": uuid.uuid4().hex,
        "exp": datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.auth_secret.get_secret_value(), algorithm=_ALGORITHM)


def start_session(db: Session, request: Request, user: User) -> str:
    """Issue a session JWT for ``user`` and record it (with the client's IP and user agent) so it shows up in
    their active-sessions list.

    The record is bookkeeping: if it cannot be written the sign-in still succeeds (the session is valid, just
    not listed), and the failure is logged. Commits.
    """
    user_id = user.id  # read before any insert: a failed flush expires ``user`` and reading it then would raise
    token = create_access_token(user_id, user.email, user.is_workspace_admin, user.name, user.token_version)
    try:
        payload = jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=[_ALGORITHM])
        session_repo.record(
            db,
            jti=payload["jti"],
            user_id=user_id,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            user_agent=request.headers.get("user-agent"),
            ip=request.client.host if request.client else None,
        )
        db.commit()
    except Exception:
        logger.exception("could not record the new session for user %s", user_id)
        db.rollback()
    return token


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.auth_secret.get_secret_value(),
            algorithms=[_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> UserOut:
    """Dependency: validate the JWT (Bearer header, else session cookie) and return the user.

    Raises 401 if missing/invalid or if token_version no longer matches (sessions revoked).
    """
    token = credentials.credentials if credentials else session
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(token)
    sub = payload.get("sub")
    email = payload.get("email")
    if not sub or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    try:
        user_id = int(sub)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    db_user = db.query(User).filter(User.id == user_id).first()
    if db_user is None or db_user.token_version != payload.get("token_version", 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    if db.get(RevokedToken, _revocation_key(token, payload)) is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    _touch_session(db, payload.get("jti"))
    # Set app.user_id for RLS self-access checks on routes that never resolve a tenant
    # (see migration 0031).
    set_session_user(db, user_id)
    # Privilege and identity come from the DB row, not the token: a claim minted before a
    # role or email change must not outlive it.
    return UserOut(
        id=user_id,
        email=db_user.email,
        name=payload.get("name"),
        is_workspace_admin=db_user.is_workspace_admin,
        github_login=db_user.github_login,
    )


def _touch_session(db: Session, jti: str | None) -> None:
    """Keep the session's "last seen" fresh, written at most once a minute. Never blocks authentication."""
    if not jti:
        return
    try:
        if session_repo.touch(db, jti):
            db.commit()
    except Exception:
        logger.warning("could not update last_seen_at for a session", exc_info=True)
        db.rollback()


def current_session_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> str | None:
    """The ``jti`` of the token this request authenticated with (None for a legacy token without one).

    Mirrors require_auth's choice of token (Bearer header, else cookie); use it only on a route that also
    depends on require_auth, which has already verified the token."""
    token = credentials.credentials if credentials else session
    if not token:
        return None
    try:
        return jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=[_ALGORITHM]).get("jti")
    except jwt.InvalidTokenError:
        return None


def _revocation_key(token: str, payload: dict) -> str:
    """Return a verified JWT's jti, or a namespaced fingerprint for a legacy token.

    Hash only the signed header and payload so equivalent encodings of the signature
    cannot bypass revocation. Callers must verify the JWT before using this key.
    """
    return payload.get("jti") or "legacy:" + hashlib.sha256(token.rsplit(".", 1)[0].encode()).hexdigest()


def revoke_presented_tokens(db: Session, *tokens: str | None) -> None:
    """Denylist each session JWT in ``tokens`` until its own expiry (single-session logout).

    Logout passes both the Bearer token and the session cookie: a browser can hold two different
    sessions (e.g. a stale localStorage token after a GitHub OAuth login), and ending only the one
    ``require_auth`` happens to prefer would leave the other alive.

    Tokens issued before ``jti`` existed are keyed by a fingerprint of their signed content.
    A missing, malformed, badly signed or already-expired token, and a user that no longer
    exists are all no-ops. Expired denylist rows are purged here since they can no longer matter.
    """
    revoked_any = False
    ended: list[str] = []
    for token in dict.fromkeys(t for t in tokens if t):
        try:
            payload = jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=[_ALGORITHM])
            jti = _revocation_key(token, payload)
            user_id = int(payload["sub"])
            expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
            continue
        if db.get(User, user_id) is None:
            continue
        db.execute(
            pg_insert(RevokedToken)
            .values(jti=jti, user_id=user_id, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=["jti"])
        )
        revoked_any = True
        ended.append(jti)
    if revoked_any:
        session_repo.forget(db, ended)
        db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(timezone.utc)))
        db.commit()


def require_workspace_admin(user: UserOut = Depends(require_auth)) -> UserOut:
    """Dependency: raises 403 if the authenticated user is not the workspace admin."""
    if not user.is_workspace_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace admin access required")
    return user
