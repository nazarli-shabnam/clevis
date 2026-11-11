"""Build the leadership digest for one org tenant: a scheduled summary (security score
movement, open risk items, recent team activity) using only data Clevis already stores.
Delivery + scheduling live in digest_sweep.py / digest_loop.py.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import ScanResult, SecurityAlert
from src.repositories import scan_results_repo
from src.services import dependabot_burndown
from src.services.sla_breach_sweep import breached_filter

logger = logging.getLogger(__name__)

_ACTIVITY_WINDOW_DAYS = 7
_MAX_RISK_ITEMS = 8


@dataclass
class DigestContent:
    org_login: str
    period_label: str
    latest_score: int | None
    previous_score: int | None
    scanned_at: datetime | None
    failing_checks: list[str] = field(default_factory=list)
    push_events_7d: int = 0
    # Open Dependabot alerts past their SLA: how many in all, and the oldest few as display lines.
    sla_breach_total: int = 0
    sla_breaches: list[str] = field(default_factory=list)

    @property
    def score_delta(self) -> int | None:
        if self.latest_score is None or self.previous_score is None:
            return None
        return self.latest_score - self.previous_score

    @property
    def is_empty(self) -> bool:
        """No scan has ever run for this org -- nothing worth emailing."""
        return self.latest_score is None


def build_digest(db: Session, *, tenant_id: int, org_login: str, period_label: str) -> DigestContent:
    """Assemble the digest for one org tenant. The caller must have set this tenant's
    RLS session context already (scan_results / repo_event_daily_counts are tenant-scoped)."""
    scans = (
        db.query(ScanResult)
        .filter(scan_results_repo.org_scope_filter(org_login, tenant_id))
        .order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
        .limit(2)
        .all()
    )
    latest = scans[0] if scans else None
    previous = scans[1] if len(scans) > 1 else None

    failing: list[str] = []
    if latest is not None and latest.checks_json:
        try:
            checks = json.loads(latest.checks_json)
        except ValueError:
            checks = []
        if isinstance(checks, list):
            # "error" counts toward failed_checks / the score in analytics_service, so it
            # must show up as a risk item here too -- otherwise the digest can say
            # "score down 14" with "open risk items: none".
            # Only checks the scan actually scored; scans stored before the "scored" stamp predate
            # hygiene checks, so for them "scored" means "not informational".
            failing = [
                str(c.get("title") or c.get("id") or "unknown check")
                for c in checks
                if isinstance(c, dict)
                and c.get("status") in ("fail", "error")
                and c.get("scored", not c.get("informational"))
            ][:_MAX_RISK_ITEMS]

    # Inclusive lower bound: the window is _ACTIVITY_WINDOW_DAYS days counting today --
    # subtracting the full 7 would span 8 daily buckets and overreport.
    since = (datetime.now(timezone.utc) - timedelta(days=_ACTIVITY_WINDOW_DAYS - 1)).date()
    push_count = db.execute(
        text(
            "SELECT COALESCE(SUM(count), 0) FROM repo_event_daily_counts "
            "WHERE tenant_id = :t AND event_type = 'push' AND day >= :since"
        ),
        {"t": tenant_id, "since": since},
    ).scalar()

    now = datetime.now(timezone.utc)
    breach_filter = (
        SecurityAlert.tenant_id == tenant_id,
        SecurityAlert.kind == "dependabot",
        SecurityAlert.state == "open",
        breached_filter(now, dependabot_burndown.sla_days()),
    )
    breach_total = db.query(SecurityAlert).filter(*breach_filter).count()
    breach_rows = (
        db.query(SecurityAlert).filter(*breach_filter)
        .order_by(SecurityAlert.created_at, SecurityAlert.id).limit(_MAX_RISK_ITEMS).all()
    )

    return DigestContent(
        org_login=org_login,
        period_label=period_label,
        latest_score=latest.score if latest else None,
        previous_score=previous.score if previous else None,
        scanned_at=latest.created_at if latest else None,
        failing_checks=failing,
        push_events_7d=int(push_count or 0),
        sla_breach_total=breach_total,
        sla_breaches=[
            f"{r.repo} #{r.number} ({r.severity}, open {(now - r.created_at).days} days)" for r in breach_rows
        ],
    )


def render_subject(content: DigestContent) -> str:
    score = "n/a" if content.latest_score is None else str(content.latest_score)
    return f"[Clevis] {content.org_login} {content.period_label} digest — security score {score}"


def render_text(content: DigestContent) -> str:
    lines = [
        f"Clevis {content.period_label} digest for {content.org_login}",
        "",
    ]
    if content.latest_score is not None:
        delta = content.score_delta
        if delta is None:
            trend = " (first recorded scan)"
        elif delta > 0:
            trend = f" (up {delta} since the previous scan)"
        elif delta < 0:
            trend = f" (down {abs(delta)} since the previous scan)"
        else:
            trend = " (no change since the previous scan)"
        lines.append(f"Security score: {content.latest_score}/100{trend}")
        if content.scanned_at is not None:
            lines.append(f"Last scanned: {content.scanned_at:%Y-%m-%d %H:%M UTC}")

    lines.append("")
    if content.failing_checks:
        lines.append(f"Open risk items ({len(content.failing_checks)}):")
        lines.extend(f"  - {title}" for title in content.failing_checks)
    else:
        lines.append("Open risk items: none in the latest scan.")

    if content.sla_breach_total:
        lines += ["", f"Dependabot alerts past their SLA ({content.sla_breach_total}):"]
        lines.extend(f"  - {item}" for item in content.sla_breaches)
        if content.sla_breach_total > len(content.sla_breaches):
            lines.append(f"  … and {content.sla_breach_total - len(content.sla_breaches)} more")

    lines += [
        "",
        f"Team activity: {content.push_events_7d} push events in the last {_ACTIVITY_WINDOW_DAYS} days.",
        "",
        "— Clevis. Adjust or disable this digest under Settings → Instance Configuration.",
    ]
    return "\n".join(lines)
