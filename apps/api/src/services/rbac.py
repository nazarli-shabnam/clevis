from collections.abc import Callable

from fastapi import Header, HTTPException

from src.core.config import settings

_LEVELS = {"viewer": 1, "analyst": 2, "admin": 3}


def require_role(required: str) -> Callable:
    def _check(x_role: str | None = Header(default=None)) -> str:
        role = x_role or settings.default_rbac_role
        if _LEVELS.get(role, 0) < _LEVELS.get(required, 0):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return role
    return _check
