from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_workspace_admin
from src.core.db import AuditLog, get_db

router = APIRouter()


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    target: str
    payload: str
    created_at: datetime


@router.get("/actions", response_model=list[str])
def list_audit_actions(
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_workspace_admin),
):
    """Every distinct action ever written, so the UI's filter can't drift from what the API records."""
    # ponytail: a DISTINCT scan of audit_logs.action; cache or index it if the table grows very large.
    return [row[0] for row in db.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    action: str | None = Query(default=None, description="Filter by action type"),
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_workspace_admin),
):
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if action:
        q = q.filter(AuditLog.action == action)
    return q.limit(limit).all()
