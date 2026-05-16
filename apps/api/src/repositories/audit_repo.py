import json

from sqlalchemy.orm import Session

from src.core.db import AuditLog


def write(db: Session, actor: str, action: str, target: str, payload: dict) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, payload=json.dumps(payload)))
    db.commit()
