"""Per-tick work for the security-alert loop.

Announces each newly ingested open critical/high alert once: it stamps ``alert_notified_at`` (so a restart
or a second replica never repeats it) and messages destinations subscribed to ``new_critical_alert``.
Secret-scanning alerts carry no severity in GitHub's payload and always count. The stamp is written whether
or not a destination exists, so subscribing later does not replay old alerts. Shape follows
waiver_expiry_sweep.py (org tenants, per-tenant advisory slot so replicas don't double-announce).
"""

import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.core.db import SecurityAlert, set_session_tenant
from src.services import notifications, sla_breach_sweep
from src.services.sweep_lock import try_acquire_sweep_slot

logger = logging.getLogger(__name__)

_SWEEP_KEY = "security_alert"
ACTOR = "system:security_alert"
# Per tick and tenant, so an initial flood is announced in a few messages rather than one huge one.
_MAX_PER_TICK = 50


def run_security_alert_sweep(db: Session) -> None:
    # tenants has no RLS (identity table) -- read freely, then scope per tenant below.
    tenants = db.execute(
        text("SELECT t.id, o.github_login FROM tenants t JOIN orgs o ON o.id = t.org_id WHERE t.kind = 'org'")
    ).fetchall()

    for tenant_id, org_login in tenants:
        try:
            set_session_tenant(db, tenant_id)
            if not try_acquire_sweep_slot(db, _SWEEP_KEY, tenant_id):
                continue
            # Same slot, same tick: alerts that just crossed their SLA window are announced alongside new ones.
            try:
                sla_breach_sweep.announce_breaches(db, tenant_id, org_login)
            except Exception:
                db.rollback()
                logger.exception("SLA breach sweep: iteration failed for tenant %d", tenant_id)
            rows = list(
                db.execute(
                    select(SecurityAlert)
                    .where(
                        SecurityAlert.tenant_id == tenant_id,
                        SecurityAlert.alert_notified_at.is_(None),
                        SecurityAlert.state == "open",
                        (SecurityAlert.kind == "secret_scanning") | SecurityAlert.severity.in_(("critical", "high")),
                    )
                    .order_by(SecurityAlert.ingested_at, SecurityAlert.id)
                    .limit(_MAX_PER_TICK)
                ).scalars()
            )
            if not rows:
                db.commit()  # release the advisory slot
                continue
            # Commit the stamp before messaging: the message is best-effort, a repeat announcement is not.
            # A non-critical alert stays unstamped, so one later raised to critical is still announced.
            # Re-check state and severity here: a worker update may have closed or downgraded an alert since
            # the SELECT, and only the rows this UPDATE actually stamps are announced.
            stamped = db.execute(
                text(
                    "UPDATE security_alerts SET alert_notified_at = now() "
                    "WHERE tenant_id = :t AND alert_notified_at IS NULL AND id = ANY(:ids) AND state = 'open' "
                    "AND (kind = 'secret_scanning' OR severity IN ('critical', 'high')) "
                    "RETURNING id"
                ),
                {"t": tenant_id, "ids": [r.id for r in rows]},
            ).scalars().all()
            db.commit()
            stamped_ids = set(stamped)
            announced = [
                {"repo": r.repo, "kind": r.kind, "number": r.number, "severity": r.severity}
                for r in rows
                if r.id in stamped_ids
            ]
            if not announced:
                continue
            try:
                notifications.notify_new_critical_alerts(db, tenant_id, org_login, announced, actor=ACTOR)
            except Exception:
                db.rollback()
                logger.exception("security alert notification failed for tenant %d", tenant_id)
        except Exception:
            db.rollback()
            logger.exception("security alert sweep: iteration failed for tenant %d", tenant_id)
