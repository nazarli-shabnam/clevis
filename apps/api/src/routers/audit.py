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
