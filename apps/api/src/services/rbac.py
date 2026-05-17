from collections.abc import Callable

from fastapi import Header, HTTPException

from src.core.config import settings

_LEVELS = {"viewer": 1, "analyst": 2, "admin": 3}


def require_role(required: str) -> Callable:
    if required not in _LEVELS:
        raise ValueError(f"Unknown role {required!r}; valid roles: {list(_LEVELS)}")
    required_level = _LEVELS[required]

    def _check(x_role: str | None = Header(default=None)) -> str:
        role = x_role or settings.default_rbac_role
        if _LEVELS.get(role, 0) < required_level:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return role
    return _check
