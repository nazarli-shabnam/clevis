"""Per-tick work for the retention loop: delete finished rows that nothing reads any more.

Three tables grow with traffic and never shrank before this sweep:

* ``webhook_deliveries`` -- raw payloads up to 5 MB each. Only rows the pipeline is finished with are
  purged (``processed``, ``dropped``, ``queue_abandoned``); ``queued`` / ``queue_failed`` rows are still
  waiting for the requeue sweep and are never touched.
* ``jobs`` -- terminal rows (``done``, ``failed``). The worker already scrubs the encrypted token from
  them; this removes the rows themselves.
* ``repo_events`` -- the normalized event store. The per-day rollup ``repo_event_daily_counts`` is kept.

``audit_logs`` and ``scan_results`` are deliberately never purged: the audit trail is a compliance record
and the scan history feeds the score trend. That is the policy, not an omission.

Windows are ``app_config`` keys (days). ``0`` switches a purge off. Each table also has a floor under the
configured value, because shorter windows would break something that still reads those rows:

* jobs >= 8 days: ``sweep_lock.has_blocking_job`` treats a job that failed within the sweep's stale window
  (up to 168 h) as "already tried", so a failed job purged earlier would be re-enqueued every tick;
* repo_events >= 100 days: backfill re-imports GitHub's last 90 days of events and skips those a webhook
  already stored, so a webhook-stored row deleted inside that window would be imported and counted twice.

Deletes run in bounded batches (a commit each) so one tick never holds a long transaction or locks a table
for long, and a first run on a big backlog drains over a few ticks. Replicas may run the sweep at the same
time: deleting a row twice is a no-op, so no cross-replica lock is needed.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg.errors import InsufficientPrivilege
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.db import set_session_tenant
from src.services import api_job_runner

logger = logging.getLogger(__name__)

BATCH_SIZE = 5_000
# Caps one table's (or one tenant's) work per tick: 500k rows. The rest is picked up on the next tick.
MAX_BATCHES = 100
MAX_DAYS = 3650
# How long one batch's DELETE may wait on a row lock before giving up.
LOCK_TIMEOUT = "5s"

# Set on API shutdown. Cancelling the loop's task does not stop the worker thread running a sweep, and the
# interpreter waits for that thread at exit, so batches check this and stop issuing deletes.
stop_requested = threading.Event()


@dataclass(frozen=True)
class RetentionPolicy:
    key: str
    default_days: int
    min_days: int


WEBHOOK_DELIVERIES = RetentionPolicy("retention_webhook_deliveries_days", default_days=30, min_days=1)
JOBS = RetentionPolicy("retention_jobs_days", default_days=30, min_days=8)
REPO_EVENTS = RetentionPolicy("retention_repo_events_days", default_days=180, min_days=100)

_TERMINAL_DELIVERY_STATUSES = ("processed", "dropped", "queue_abandoned")
_TERMINAL_JOB_STATUSES = ("done", "failed")

# No ORDER BY in the subqueries: which rows go first is irrelevant, and sorting would make Postgres find and
# order every expired row for each batch instead of stopping after the first BATCH_SIZE.
_DELETE_DELIVERIES = (
    "DELETE FROM webhook_deliveries WHERE id IN ("
    "SELECT id FROM webhook_deliveries WHERE status = ANY(:statuses) AND received_at < :cutoff LIMIT :limit)"
)
_DELETE_JOBS = (
    "DELETE FROM jobs WHERE id IN ("
    "SELECT id FROM jobs WHERE status = ANY(:statuses) AND updated_at < :cutoff LIMIT :limit)"
)
_DELETE_REPO_EVENTS = (
    "DELETE FROM repo_events WHERE id IN ("
    "SELECT id FROM repo_events WHERE tenant_id = :tenant_id AND occurred_at < :cutoff LIMIT :limit)"
)


def retention_days(policy: RetentionPolicy) -> int | None:
    """The configured window in days, or None when the purge is switched off (``0``). An unset or
    unreadable value falls back to the default; a value under the policy's floor is raised to it."""
    raw = get_config(policy.key, str(policy.default_days))
    try:
        days = int(raw)
    except ValueError:
        logger.warning("%s %r is not an integer; using %d", policy.key, raw, policy.default_days)
        return policy.default_days
    if days == 0:
        return None
    if days < 0:
        logger.warning("%s %d is negative; using %d", policy.key, days, policy.default_days)
        return policy.default_days
    return max(policy.min_days, min(MAX_DAYS, days))


def _delete_in_batches(db: Session, sql: str, params: dict) -> int:
    total = 0
    for _ in range(MAX_BATCHES):
        if stop_requested.is_set():
            break
        # Transaction-local, so it lapses at the commit below (or the caller's rollback). A DELETE stuck behind
        # another transaction's row lock raises instead of blocking; run_retention_sweep rolls back and moves on.
        db.execute(text("SELECT set_config('lock_timeout', :t, true)"), {"t": LOCK_TIMEOUT})
        deleted = db.execute(text(sql), {**params, "limit": BATCH_SIZE}).rowcount
        db.commit()
        total += deleted
        if deleted < BATCH_SIZE:
            break
    else:
        logger.info("retention batch cap reached after %d rows; the rest is purged on the next tick", total)
    return total


def _purge_deliveries(db: Session, cutoff: datetime) -> int:
    return _delete_in_batches(db, _DELETE_DELIVERIES, {"statuses": list(_TERMINAL_DELIVERY_STATUSES), "cutoff": cutoff})


def _purge_jobs(db: Session, cutoff: datetime) -> int:
    return _delete_in_batches(db, _DELETE_JOBS, {"statuses": list(_TERMINAL_JOB_STATUSES), "cutoff": cutoff})


def _purge_repo_events(db: Session, cutoff: datetime) -> int:
    # repo_events has strict tenant RLS, so it is purged one tenant at a time with that tenant's
    # context set. tenants has no RLS: read it freely.
    total = 0
    for (tenant_id,) in db.execute(text("SELECT id FROM tenants")).fetchall():
        set_session_tenant(db, tenant_id)
        total += _delete_in_batches(db, _DELETE_REPO_EVENTS, {"tenant_id": tenant_id, "cutoff": cutoff})
    return total


def run_retention_sweep(db: Session) -> None:
    now = datetime.now(timezone.utc)
    try:
        reaped = api_job_runner.reap_orphans(db)
        if reaped:
            logger.info("failed %d API job(s) orphaned by a restart", reaped)
    except Exception:
        db.rollback()
        logger.exception("reaping orphaned API jobs failed")
    purges = (
        (WEBHOOK_DELIVERIES, _purge_deliveries),
        (JOBS, _purge_jobs),
        # Last: it sets a tenant context on the session (the loop resets it afterwards).
        (REPO_EVENTS, _purge_repo_events),
    )
    purged: dict[str, int] = {}
    for policy, purge in purges:
        days = retention_days(policy)
        if days is None:
            continue
        try:
            purged[policy.key] = purge(db, now - timedelta(days=days))
        except Exception as exc:
            # One table failing (a lock timeout, a transient error) must not stop the others.
            db.rollback()
            if isinstance(getattr(exc, "orig", None), InsufficientPrivilege):
                # The API's database role lacks DELETE on the table: a grant is missing, not a transient fault.
                logger.error(
                    "retention purge for %s skipped: the database role may not DELETE from that table (%s)",
                    policy.key,
                    exc.orig,
                )
            else:
                logger.exception("retention purge failed for %s", policy.key)
    if any(purged.values()):
        logger.info("retention sweep purged rows: %s", purged)
