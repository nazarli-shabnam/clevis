"""
Instance configuration router — /config

GET  /config          Returns all app_config key/value pairs (any authenticated user)
PUT  /config/{key}    Updates a config value (owner only)
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.core.app_config import _ACCEPTED_KEYS, read_all, set_config
from src.core.auth import UserOut, require_auth, require_owner

logger = logging.getLogger(__name__)
router = APIRouter()

_INT_KEYS = {"worker_poll_seconds"}
_JSON_KEYS = {"cors_origins"}


class ConfigValue(BaseModel):
    value: str


@router.get("", response_model=dict[str, str])
def get_all_config(_user: UserOut = Depends(require_auth)) -> dict[str, str]:
    """Return all instance config values. Requires authentication."""
    return read_all()


@router.put("/{key}", response_model=dict[str, str])
def update_config(
    key: str,
    body: ConfigValue,
    _user: UserOut = Depends(require_owner),
) -> dict[str, str]:
    """Update a single config value. Owner only."""
    if key not in _ACCEPTED_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown config key: {key!r}")

    # Type validation
    if key in _INT_KEYS:
        try:
            parsed_int = int(body.value)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"{key} must be an integer")
        if parsed_int < 1:
            raise HTTPException(status_code=422, detail=f"{key} must be at least 1")
    elif key == "github_api_base":
        candidate = body.value.strip()
        if not candidate.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=422,
                detail=f"{key} must be an http(s) URL",
            )
    elif key in _JSON_KEYS:
        try:
            parsed = json.loads(body.value)
            if not isinstance(parsed, list):
                raise ValueError("must be a JSON array")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{key}: {exc}")

    try:
        set_config(key, body.value)
    except Exception:
        logger.exception("Failed to update config key %r", key)
        raise HTTPException(status_code=500, detail="Failed to update config")

    return read_all()
