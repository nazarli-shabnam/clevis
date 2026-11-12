"""Announces Dependabot alerts that have just passed their SLA (``dependabot_sla_*_days``), once each.

Runs inside the security-alert sweep's per-tenant slot (see security_alert_sweep.py), so replicas do not
double-announce. ``sla_notified_at`` is stamped *before* messaging, whether or not a destination exists, so
a restart never repeats a message and subscribing later does not replay old breaches. An alert is breached
when it is still open and older than its severity's window; the in-app feed derives its own item from the
same rule and needs nothing from here.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from src.core.db import SecurityAlert
from src.services import dependabot_burndown, notifications

logger = logging.getLogger(__name__)

ACTOR = "system:sla_breach"
# Per tick and tenant, so a first run over a large backlog is a few messages rather than one huge one.
_MAX_PER_TICK = 50


def breached_filter(now: datetime, windows: dict[str, int | None]):
    """SQL condition: open Dependabot alerts older than their severity's SLA window (no window = never breached)."""
    clauses = [
        (SecurityAlert.severity == severity) & (SecurityAlert.created_at <= now - timedelta(days=days))
        for severity, days in windows.items()
        if days is not None
    ]
    return or_(*clauses) if clauses else SecurityAlert.id.is_(None)


def announce_breaches(db: Session, tenant_id: int, org_login: str) -> None:
    """Caller holds the tenant's sweep slot and has set the tenant session context."""
    now = datetime.now(timezone.utc)
    rows = list(
        db.execute(
            select(SecurityAlert)
            .where(
                SecurityAlert.tenant_id == tenant_id,
                SecurityAlert.kind == "dependabot",
                SecurityAlert.state == "open",
                SecurityAlert.sla_notified_at.is_(None),
                breached_filter(now, dependabot_burndown.sla_days()),
            )
            .order_by(SecurityAlert.created_at, SecurityAlert.id)
            .limit(_MAX_PER_TICK)
        ).scalars()
    )
    if not rows:
        return
    # Stamp first and announce only what this UPDATE stamped: a worker update may have closed an alert since the SELECT.
    stamped = set(
        db.execute(
            text(
                "UPDATE security_alerts SET sla_notified_at = now() "
                "WHERE tenant_id = :t AND sla_notified_at IS NULL AND state = 'open' AND id = ANY(:ids) RETURNING id"
            ),
            {"t": tenant_id, "ids": [r.id for r in rows]},
        ).scalars()
    )
    db.commit()
    windows = dependabot_burndown.sla_days()
    breaches = [
        {"repo": r.repo, "number": r.number, "severity": r.severity, "sla_days": windows.get(r.severity)}
        for r in rows
        if r.id in stamped
    ]
    if not breaches:
        return
    try:
        notifications.notify_sla_breaches(db, tenant_id, org_login, breaches, actor=ACTOR)
    except Exception:
        db.rollback()
        logger.exception("SLA breach notification failed for tenant %d", tenant_id)
