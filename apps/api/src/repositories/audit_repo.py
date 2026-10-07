import json

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from src.core.db import AuditLog


def write(
    db: Session, actor: str, action: str, target: str, payload: dict, tenant_id: int | None = None,
    *, commit: bool = True,
) -> None:
    """commit=False flushes into the caller's transaction, so a mutation and its audit row
    commit (or roll back) together."""
    # audit_logs RLS is strict equality on app.tenant_id, and many callers never set it. SET LOCAL
    # to exactly the value being written is safe (not caller-controlled) and scoped to this transaction.
    if tenant_id is not None:
        db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
    db.add(AuditLog(actor=actor, action=action, target=target, payload=json.dumps(payload), tenant_id=tenant_id))
    if commit:
        db.commit()
    else:
        db.flush()


# Audit actions written when an automation really ran (not a dry run or a settings save). Used for the
# "run your first automation" onboarding step.
AUTOMATION_RUN_ACTIONS = (
    "automation.workflow.dispatch",
    "branch_protection.bulk_apply",
    "dependabot_triage.run",
    "cache.clear.queued",
    "security.remediate",
    "workflow_lint.autofix_pr",
    "pr_nudge.sweep",
)


def has_any_action(db: Session, tenant_id: int, actions: tuple[str, ...]) -> bool:
    """Whether the tenant has at least one audit row for any of `actions`. The caller must have set the
    tenant session context (audit_logs RLS is strict equality on it)."""
    return db.execute(
        select(exists().where(AuditLog.tenant_id == tenant_id, AuditLog.action.in_(actions)))
    ).scalar_one()
