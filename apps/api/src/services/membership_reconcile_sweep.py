"""Periodically re-sync each org-kind tenant's org_members/repo_collaborators state via a
full GitHub roster poll.

Mirrors gap_heal_sweep.py's shape, except only org-kind tenants have org membership to
reconcile (personal tenants are skipped outright, not just left with no cursor row).

This poll is not a fallback for missed webhooks the way gap-healing is for activity sync:
an org member's role changing and 2FA status have zero webhook coverage, ever -- this
sweep is the only source of truth for those two fields, permanently.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.db import set_session_tenant
from src.services import membership_reconcile_service
from src.services.sweep_lock import try_acquire_sweep_slot
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

logger = logging.getLogger(__name__)

_JOB_TYPE = "github.reconcile_org_membership"


def _read_stale_hours() -> int:
    raw = get_config("membership_reconcile_stale_hours", "24")
    try:
        return max(1, min(168, int(raw)))
    except ValueError:
        logger.warning("membership_reconcile_stale_hours %r is not an integer; using 24", raw)
        return 24


def _has_active_reconcile_job(db: Session, tenant_id: int) -> bool:
    """Same reasoning as gap_heal_sweep.py's _has_active_backfill_job: the cursor only
    advances once a job reaches a terminal state, so without this a slow-to-finish job would
    still look stale on every subsequent sweep tick and get re-enqueued repeatedly."""
    row = db.execute(
        text(
            "SELECT 1 FROM jobs WHERE job_type = :job_type "
            "AND status IN ('queued', 'processing') "
            "AND (payload::jsonb ->> 'tenant_id')::int = :tenant_id LIMIT 1"
        ),
        {"job_type": _JOB_TYPE, "tenant_id": tenant_id},
    ).fetchone()
    return row is not None


def run_membership_reconcile_sweep(db: Session) -> None:
    stale_hours = _read_stale_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)

    # tenants/orgs have no RLS of their own (identity tables, not tenant-scoped data -- see
    # migration 0030's table list), so this read needs no session tenant context.
    org_tenants = db.execute(
        text("SELECT t.id, t.org_id, o.github_login FROM tenants t JOIN orgs o ON o.id = t.org_id WHERE t.kind = 'org'")
    ).fetchall()

    for tenant_id, org_id, org_login in org_tenants:
        if not org_login:
            continue

        # org_membership_sync_cursors is RLS-enabled -- must be set before reading this
        # tenant's own cursor row, one tenant at a time.
        set_session_tenant(db, tenant_id)
        cursor_row = db.execute(
            text("SELECT last_synced_at FROM org_membership_sync_cursors WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).fetchone()
        if cursor_row is not None:
            (last_synced_at,) = cursor_row
            if last_synced_at is not None and last_synced_at >= cutoff:
                continue
        # Acquired before the active-job check so the whole check-then-enqueue window for
        # this tenant is mutually exclusive across concurrent sweep passes -- a losing
        # process skips this tenant this tick rather than re-deriving a stale answer.
        if not try_acquire_sweep_slot(db, _JOB_TYPE, tenant_id):
            continue
        if _has_active_reconcile_job(db, tenant_id):
            db.commit()  # nothing to persist; releases the advisory lock promptly
            continue

        try:
            token = resolve_org_token(db, org_id=org_id, account_login=org_login, client_token=None)
            membership_reconcile_service.enqueue(db, tenant_id=tenant_id, org_login=org_login, token=token)
        except NoGitHubTokenAvailable as exc:
            db.commit()  # nothing to persist; releases the advisory lock promptly
            logger.warning("membership-reconcile sweep skipping %s (tenant %d): %s", org_login, tenant_id, exc)
        except Exception:
            # A DB-level error here would leave this shared Session's transaction aborted --
            # roll back so the sweep can keep going for the remaining tenants.
            db.rollback()
            logger.exception("membership-reconcile sweep failed to enqueue for %s (tenant %d)", org_login, tenant_id)
