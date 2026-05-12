from fastapi import Header, HTTPException

from src.core.config import settings


def require_role(required: str, x_role: str | None = Header(default=None)) -> str:
    role = x_role or settings.default_rbac_role
    levels = {"viewer": 1, "analyst": 2, "admin": 3}
    if levels.get(role, 0) < levels.get(required, 0):
        raise HTTPException(status_code=403, detail="Insufficient role")
    return role
