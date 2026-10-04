"""Bearer-token auth for machine callers (CI jobs): ``Authorization: Bearer clv_...``."""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.repositories import api_token_repo
from src.repositories.api_token_repo import ResolvedToken


def require_api_token(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> ResolvedToken:
    scheme, _, value = (authorization or "").partition(" ")
    resolved = api_token_repo.resolve(db, value.strip()) if scheme.lower() == "bearer" and value.strip() else None
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # The RLS policies key off this; tokens are bound to exactly one org's tenant.
    db.execute(text(f"SET app.tenant_id = {int(resolved.tenant_id)}"))
    api_token_repo.touch(db, resolved.token_id)
    return resolved
