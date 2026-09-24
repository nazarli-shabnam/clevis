import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import AuditLog


def write(db: Session, actor: str, action: str, target: str, payload: dict, tenant_id: int | None = None) -> None:
    # audit_logs RLS is strict equality on app.tenant_id, and many callers never set it. SET LOCAL
    # to exactly the value being written is safe (not caller-controlled) and scoped to this transaction.
    if tenant_id is not None:
        db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
    db.add(AuditLog(actor=actor, action=action, target=target, payload=json.dumps(payload), tenant_id=tenant_id))
    db.commit()
