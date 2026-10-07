"""Per-tick work for the scheduled-scan loop.

Re-scans org tenants whose latest scan is older than their cadence (see scan_schedule), storing the
result and alerting on a drop exactly like a manual scan (scan_service). Shape follows digest_sweep.py.

Safety rails, because this spends GitHub API quota nobody is watching:
- installation tokens only (``client_token=None``): an org without a GitHub App is never scanned, and a
  pasted token is never used for an unattended run;
- at most MAX_SCANS_PER_TICK scans per tick, oldest first, so a large instance spreads its usage;
- a per-tenant advisory lock plus a re-check of "is it still due" after taking it, so two API replicas
  don't scan the same org twice;
- a failed scan is recorded (``scan.scheduled_failed``) and retried only after FAILURE_RETRY, so a broken
  org doesn't burn a slot on every tick.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import Org, set_session_tenant
from src.repositories import audit_repo, scan_results_repo
from src.services import scan_service
from src.services.analytics_service import get_overview, org_scores_hygiene
from src.services.scan_schedule import CADENCE_INTERVAL, effective_cadence, instance_cadence
from src.services.sweep_lock import try_acquire_sweep_slot
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

logger = logging.getLogger(__name__)

_SWEEP_KEY = "scheduled_scan"
ACTOR = "system:scheduled_scan"
MAX_SCANS_PER_TICK = 3
# Same grace the digest uses, so a scan doesn't drift a poll-interval later every period.
_GRACE = timedelta(hours=1)
FAILURE_RETRY = timedelta(hours=6)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _last_failure_at(db: Session, tenant_id: int) -> datetime | None:
    row = db.execute(
        text("SELECT MAX(created_at) FROM audit_logs WHERE tenant_id = :t AND action = 'scan.scheduled_failed'"),
        {"t": tenant_id},
    ).fetchone()
    return _aware(row[0]) if row else None


def _due_since(db: Session, org_login: str, tenant_id: int, cadence: str, now: datetime) -> datetime | None:
    """None when the org is not due, else the time of its latest scan (or the epoch when never scanned),
    used to scan the longest-waiting orgs first."""
    latest = scan_results_repo.latest_with_checks(db, org_login, tenant_id)
    last_scan = _aware(datetime.fromisoformat(latest["scanned_at"])) if latest and latest["scanned_at"] else None
    if last_scan is not None and now - last_scan < CADENCE_INTERVAL[cadence] - _GRACE:
        return None
    failed = _last_failure_at(db, tenant_id)
    if failed is not None and now - failed < FAILURE_RETRY:
        return None
    return last_scan or datetime.fromtimestamp(0, tz=timezone.utc)


def _scan_one(db: Session, org_id: int, tenant_id: int, org_login: str, cadence: str, now: datetime) -> bool:
    """Scan one org if it is still due once this replica holds its lock. True when a scan was stored."""
    set_session_tenant(db, tenant_id)
    if not try_acquire_sweep_slot(db, _SWEEP_KEY, tenant_id):
        return False
    # Re-check under the lock: another replica may have scanned between the candidate query and now.
    if _due_since(db, org_login, tenant_id, cadence, now) is None:
        db.commit()
        return False
    org = db.get(Org, org_id)
    try:
        token = resolve_org_token(db, org_id=org_id, account_login=org_login, client_token=None)
    except NoGitHubTokenAvailable:
        logger.info("scheduled scan: %s has no GitHub App installation token; skipping", org_login)
        db.commit()
        return False
    try:
        result = get_overview(
            owner=org_login, token=token, account_type="Organization", score_hygiene=org_scores_hygiene(org)
        )
    except Exception as exc:
        db.rollback()
        logger.exception("scheduled scan failed for %s", org_login)
        audit_repo.write(
            db, actor=ACTOR, action="scan.scheduled_failed", target=org_login,
            payload={"error": type(exc).__name__}, tenant_id=tenant_id,
        )
        return False
    previous = scan_service.persist_scan_and_alert(db, org, result, tenant_id, actor=ACTOR)
    audit_repo.write(
        db, actor=ACTOR, action="scan.scheduled", target=org_login,
        payload={"cadence": cadence, "score": result["score"], "previous_score": previous["score"] if previous else None},
        tenant_id=tenant_id,
    )
    return True


def run_scheduled_scan_sweep(db: Session) -> None:
    instance = instance_cadence()
    now = datetime.now(timezone.utc)

    # tenants has no RLS (identity table) -- read freely, then scope per tenant below.
    rows = db.execute(
        text(
            "SELECT t.id, o.id, o.github_login, o.scheduled_scans FROM tenants t "
            "JOIN orgs o ON o.id = t.org_id WHERE t.kind = 'org'"
        )
    ).fetchall()

    candidates: list[tuple[datetime, int, int, str, str]] = []
    for tenant_id, org_id, org_login, override in rows:
        cadence = effective_cadence(override, instance)
        if cadence is None:
            continue
        try:
            set_session_tenant(db, tenant_id)
            since = _due_since(db, org_login, tenant_id, cadence, now)
        except Exception:
            db.rollback()
            logger.exception("scheduled scan: due check failed for tenant %d", tenant_id)
            continue
        if since is not None:
            candidates.append((since, tenant_id, org_id, org_login, cadence))

    scanned = 0
    for _, tenant_id, org_id, org_login, cadence in sorted(candidates)[:MAX_SCANS_PER_TICK * 3]:
        if scanned >= MAX_SCANS_PER_TICK:
            break
        try:
            if _scan_one(db, org_id, tenant_id, org_login, cadence, now):
                scanned += 1
        except Exception:
            db.rollback()
            logger.exception("scheduled scan: iteration failed for tenant %d", tenant_id)
