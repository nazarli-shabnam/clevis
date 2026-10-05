"""Bearer-token auth for machine callers (CI jobs): ``Authorization: Bearer clv_...``."""

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.repositories import api_token_repo
from src.repositories.api_token_repo import ResolvedToken


# Scopes in increasing power. Only "read" exists today; the ordering is what lets a future broader
# scope satisfy "read" without every endpoint being edited, while an unknown scope satisfies nothing.
_SCOPE_RANK = {"read": 0}


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
    return resolved


def require_scope(required: str):
    """Dependency factory: authenticate the bearer token, then check it carries `required` (403 otherwise).

    Every machine endpoint must use this (not bare ``require_api_token``) so a scope added later is
    enforced by default instead of silently ignored."""
    required_rank = _SCOPE_RANK[required]

    def dependency(token: ResolvedToken = Depends(require_api_token), db: Session = Depends(get_db)) -> ResolvedToken:
        rank = _SCOPE_RANK.get(token.scope)
        if rank is None or rank < required_rank:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"API token lacks the '{required}' scope")
        # Only a token that is actually allowed in counts as "used".
        api_token_repo.touch(db, token.token_id)
        return token

    return dependency
