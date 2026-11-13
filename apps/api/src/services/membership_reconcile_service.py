"""Enqueue a one-shot org membership reconciliation.

Mirrors backfill_service.enqueue (Fernet-encrypt the token for the worker payload, insert
a `jobs` row for the worker's poll loop). apps/worker/src/membership_reconcile.py +
worker.py's _handle_reconcile_org_membership do the actual GitHub calls and DB writes.
"""

from sqlalchemy.orm import Session

from src.repositories import job_repo
from src.services.job_token import job_token_fields


def enqueue(db: Session, *, tenant_id: int, org_login: str, token: str) -> int:
    return job_repo.enqueue(
        db,
        "github.reconcile_org_membership",
        {
            "tenant_id": tenant_id,
            "org_login": org_login,
            **job_token_fields(token),
        },
        tenant_id=tenant_id,
    )
