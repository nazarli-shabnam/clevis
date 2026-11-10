"""Enqueue a one-shot install-time activity backfill.

Mirrors cache_service.clear()'s job-enqueue pattern: Fernet-encrypt the token for the
worker payload, then insert a `jobs` row for the worker's SELECT ... FOR UPDATE SKIP
LOCKED poll loop. See apps/worker/src/backfill.py and worker.py's
_handle_backfill_repo_events for the worker-side handler.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core._crypto import encrypt_job_token
from src.core.config import settings
from src.repositories import job_repo
from src.services.sweep_lock import has_blocking_job

ALERTS_JOB_TYPE = "github.backfill_security_alerts"
# An alert backfill that failed, or finished without recording anything, is not re-enqueued for this long (it
# would otherwise mint a new job and token on every install/gap-heal pass).
_ALERTS_RETRY_AFTER = timedelta(hours=24)


def enqueue(db: Session, *, tenant_id: int, account_login: str, account_type: str, token: str) -> int:
    """Enqueue the activity backfill and, if this tenant's open security alerts were never read, the alert
    backfill too. Both call sites (install time, gap-heal) go through here, so existing installs pick up the
    alert backfill on their next gap-heal pass."""
    encrypted_token = encrypt_job_token(token, settings.job_secret_key.get_secret_value())
    _enqueue_security_alerts_if_needed(
        db, tenant_id=tenant_id, account_login=account_login, account_type=account_type, encrypted_token=encrypted_token
    )
    return job_repo.enqueue(
        db,
        "github.backfill_repo_events",
        {
            "tenant_id": tenant_id,
            "account_login": account_login,
            "account_type": account_type,
            "token": encrypted_token,
        },
        tenant_id=tenant_id,
    )


def _enqueue_security_alerts_if_needed(
    db: Session, *, tenant_id: int, account_login: str, account_type: str, encrypted_token: str
) -> None:
    """One-shot: skipped once any (repo, kind) is recorded as read (security_alert_syncs), while a job is queued
    or running, or after a recent failure. New alerts arrive by webhook, so this only has to run until the
    first success."""
    # security_alert_syncs is tenant-isolated: set the context for this read (SET LOCAL, ends with the commit).
    db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
    already = db.execute(
        text("SELECT 1 FROM security_alert_syncs WHERE tenant_id = :t LIMIT 1"), {"t": tenant_id}
    ).fetchone()
    if already is not None:
        return
    cutoff = datetime.now(timezone.utc) - _ALERTS_RETRY_AFTER
    if has_blocking_job(db, ALERTS_JOB_TYPE, tenant_id, cutoff):
        return
    # A job that finished but recorded nothing (e.g. the token can read no alerts at all) is not retried every
    # pass either: it would cost a request per repo and kind each time.
    ran_recently = db.execute(
        text(
            "SELECT 1 FROM jobs WHERE job_type = :job_type AND tenant_id = :t AND status = 'done' "
            "AND updated_at >= :cutoff LIMIT 1"
        ),
        {"job_type": ALERTS_JOB_TYPE, "t": tenant_id, "cutoff": cutoff},
    ).fetchone()
    if ran_recently is not None:
        return
    job_repo.enqueue(
        db,
        ALERTS_JOB_TYPE,
        {
            "tenant_id": tenant_id,
            "account_login": account_login,
            "account_type": account_type,
            "token": encrypted_token,
        },
        tenant_id=tenant_id,
    )
