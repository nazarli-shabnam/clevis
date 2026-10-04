"""JWT helpers and FastAPI auth dependencies (org-scoped roles live in src/core/rbac.py).

require_auth checks token_version (log out everywhere) and the per-token ``jti`` denylist (single-session
logout) against the DB on every request so revoked sessions end immediately.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import RevokedToken, User, get_db, set_session_user

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
    # Tokens issued before `jti` existed carry none and stay valid until they expire (or the user
    # revokes all sessions); every token minted since is checked against the logout denylist.
    jti = payload.get("jti")
    if jti and db.get(RevokedToken, jti) is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
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


def revoke_presented_tokens(db: Session, *tokens: str | None) -> None:
    """Denylist each session JWT in ``tokens`` until its own expiry (single-session logout).

    Logout passes both the Bearer token and the session cookie: a browser can hold two different
    sessions (e.g. a stale localStorage token after a GitHub OAuth login), and ending only the one
    ``require_auth`` happens to prefer would leave the other alive.

    Best-effort and silent per token: a missing, malformed, badly signed or already-expired token,
    a token from before ``jti`` existed, and a user that no longer exists are all no-ops, because
    logout must always succeed and there is nothing left to revoke in any of those cases. Expired
    denylist rows are purged here since they can no longer matter.
    """
    revoked_any = False
    for token in dict.fromkeys(t for t in tokens if t):
        try:
            payload = jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=[_ALGORITHM])
            jti = payload["jti"]
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
    if revoked_any:
        db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(timezone.utc)))
        db.commit()


def require_workspace_admin(user: UserOut = Depends(require_auth)) -> UserOut:
    """Dependency: raises 403 if the authenticated user is not the workspace admin."""
    if not user.is_workspace_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace admin access required")
    return user
