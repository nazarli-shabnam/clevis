from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_workspace_admin
from src.core.db import AuditLog, get_db
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


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    action: str | None = Query(default=None, description="Filter by action type"),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_workspace_admin),
):
    # SECURITY DEFINER (migration 0068): this is the instance-wide view, and there is no tenant
    # context to set, so under clevis_api RLS a plain query would always return [].
    return (
        db.query(AuditLog)
        .from_statement(text("SELECT * FROM audit_logs_for_admin(:action, :limit)"))
        .params(action=action or None, limit=limit)
        .all()
    )
