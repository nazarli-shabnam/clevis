from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_workspace_admin
from src.core.db import get_db
from src.repositories import audit_repo
from src.schemas.audit import AuditLogOut

router = APIRouter()


@router.get("/actions", response_model=list[str])
def list_audit_actions(
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_workspace_admin),
):
    """Every distinct action ever written, so the UI's filter can't drift from what the API records."""
    # ponytail: a DISTINCT scan of audit_logs.action; cache or index it if the table grows very large.
    # SECURITY DEFINER (migration 0068): the instance-wide view has no tenant to set under clevis_api RLS.
    return list(db.execute(text("SELECT * FROM audit_log_actions()")).scalars())


def _utc(value: datetime | None) -> datetime | None:
    """A timestamp without an offset is read as UTC, not in the DB session's timezone."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    action: str | None = Query(default=None, description="Filter by action type"),
    action_prefix: str | None = Query(default=None, max_length=100, description="Actions starting with this, e.g. 'token.'"),
    actor: str | None = Query(default=None, max_length=320, description="Exact actor (case-insensitive)"),
    target: str | None = Query(default=None, max_length=200, description="Target containing this text"),
    since: datetime | None = Query(default=None, description="Created at or after this time"),
    until: datetime | None = Query(default=None, description="Created before this time"),
    before_id: int | None = Query(default=None, ge=1, le=2_147_483_647, description="Cursor: only rows older than this id"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_workspace_admin),
):
    since, until = _utc(since), _utc(until)
    if since is not None and until is not None and since >= until:
        raise HTTPException(status_code=422, detail="'since' must be earlier than 'until'")
    # Read through a SECURITY DEFINER function (migrations 0075/0078): this is the instance-wide view, and
    # there is no tenant context to set, so under clevis_api RLS a plain query would always return [].
    return audit_repo.list_for_admin(
        db,
        action=action,
        action_prefix=action_prefix,
        actor=actor,
        target=target,
        since=since,
        until=until,
        before_id=before_id,
        limit=limit,
    )
