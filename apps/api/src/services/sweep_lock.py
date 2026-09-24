"""Shared advisory-lock helper for the periodic sweep loops. Both do a plain
check-then-enqueue for whether a job is already active for a tenant -- safe within a
single sweep pass, but not across two concurrent passes (e.g. two API replicas), where
both could pass the check before either enqueues, producing a duplicate job.

pg_try_advisory_xact_lock serializes the check-then-enqueue around a (job_type, tenant_id)
key, transaction-scoped (released at the caller's next commit/rollback). Non-blocking (the
`_try` variant): a sweep tick that loses the race should skip this tenant and retry next
tick, not stall waiting for another replica.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


def try_acquire_sweep_slot(db: Session, job_type: str, tenant_id: int) -> bool:
    """True if this session now holds the (job_type, tenant_id) advisory lock for the
    current transaction. False means another connection holds it right now -- skip this
    tenant this tick."""
    return bool(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:job_type), :tenant_id)"),
            {"job_type": job_type, "tenant_id": tenant_id},
        ).scalar()
    )
