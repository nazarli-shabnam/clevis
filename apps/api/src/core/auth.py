"""
JWT helpers and FastAPI auth dependencies.

Two access levels:
  - require_auth  — any authenticated user (valid JWT)
  - require_owner — authenticated user with is_owner=True (instance config only)
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from src.core.config import settings

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 30

# HTTPBearer correctly models "send Bearer token in Authorization header".
# OAuth2PasswordBearer would misrepresent /auth/login as a form-encoded OAuth2 flow.
_http_bearer = HTTPBearer(auto_error=False)


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    is_owner: bool


def create_access_token(user_id: int, email: str, is_owner: bool, name: str | None = None) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "is_owner": is_owner,
        "name": name,
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
) -> UserOut:
    """Dependency: validates JWT and returns the current user. Raises 401 if missing/invalid."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = decode_access_token(credentials.credentials)
    sub = payload.get("sub")
    email = payload.get("email")
    if not sub or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    try:
        user_id = int(sub)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    return UserOut(
        id=user_id,
        email=email,
        name=payload.get("name"),
        is_owner=bool(payload.get("is_owner", False)),
    )


def require_owner(user: UserOut = Depends(require_auth)) -> UserOut:
    """Dependency: raises 403 if the authenticated user is not the instance owner."""
    if not user.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    return user
