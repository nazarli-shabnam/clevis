"""Dependabot and code-scanning alert burn-down from ingested ``security_alerts`` rows: open alerts by severity
over time, median/oldest age, and SLA breaches per severity and per repo. Each kind has its own SLA windows
(``dependabot_sla_*_days`` and ``code_scanning_sla_*_days``).

Limits worth knowing (surfaced to users in the UI): rows come from webhooks plus the open-alert backfill
(``github.backfill_security_alerts``, migration 0080; at install and again about monthly), which lists only
open alerts. The table keeps no separate "closed at" timestamp, so a closed alert's ``updated_at`` stands in
for its close time, and when the backfill closes a stored alert whose close webhook was missed it sets
``updated_at = NOW()``, so such an alert's trend close day can be the sync day rather than the real one. An alert reopened later is shown
as open for its whole life in the trend.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from src.core.app_config import get_config

SEVERITIES = ("critical", "high", "medium", "low")
TREND_MAX_DAYS = 90
_MAX_REPO_ROWS = 50


SLA_DEFAULTS = {"critical": 7, "high": 30}
# security_alerts.kind values that have an SLA, and how the UI and messages name them.
KINDS = ("dependabot", "code_scanning")
KIND_LABELS = {"dependabot": "Dependabot", "code_scanning": "Code scanning"}


def sla_days(kind: str = "dependabot") -> dict[str, int | None]:
    """Critical/high SLA windows for ``kind`` from instance config (``<kind>_sla_<severity>_days``); a bad
    stored value falls back to the default."""
    out: dict[str, int | None] = {}
    for severity, default in SLA_DEFAULTS.items():
        try:
            value = int(get_config(f"{kind}_sla_{severity}_days", str(default)))
        except ValueError:
            value = default
        out[severity] = value if 1 <= value <= 3650 else default
    return out


def sla_by_kind(kinds=KINDS) -> dict[str, dict[str, int | None]]:
    return {kind: sla_days(kind) for kind in kinds}


def shared_windows(by_kind: dict[str, dict[str, int | None]]) -> dict[str, int | None]:
    """The window to show per severity when rows of several kinds are mixed: the common value, or None when
    the kinds differ (breaches are still counted per row against its own kind's window)."""
    out: dict[str, int | None] = {}
    for severity in SLA_DEFAULTS:
        values = {windows.get(severity) for windows in by_kind.values()}
        out[severity] = values.pop() if len(values) == 1 else None
    return out


def _window(row, flat: dict[str, int | None], by_kind: dict[str, dict[str, int | None]] | None) -> int | None:
    if by_kind is None:
        return flat.get(row.severity)
    return by_kind.get(row.kind, {}).get(row.severity)


def _age_days(created: datetime, now: datetime) -> float:
    return max((now - created).total_seconds() / 86400, 0.0)


def _closed_at(row) -> datetime | None:
    return None if row.state == "open" else row.updated_at


def summarize(rows: list, now: datetime, sla_days: dict[str, int | None], by_kind=None) -> list[dict]:
    """``sla_days`` is the window shown per severity; with ``by_kind`` (rows of several kinds) each row is
    judged against its own kind's window instead."""
    out = []
    for severity in SEVERITIES:
        open_rows = [r for r in rows if r.state == "open" and r.severity == severity]
        ages = [_age_days(r.created_at, now) for r in open_rows]
        breaches = 0
        for r, age in zip(open_rows, ages):
            window = _window(r, sla_days, by_kind)
            breaches += window is not None and age > window
        out.append(
            {
                "severity": severity,
                "open": len(ages),
                "median_age_days": round(statistics.median(ages), 1) if ages else None,
                "oldest_age_days": round(max(ages), 1) if ages else None,
                "sla_days": sla_days.get(severity),
                "breaches": breaches,
            }
        )
    return out


def by_repo(rows: list, now: datetime, sla_days: dict[str, int | None], by_kind=None) -> list[dict]:
    repos: dict[str, dict] = defaultdict(lambda: {"open": dict.fromkeys(SEVERITIES, 0), "oldest": None, "breaches": 0})
    for r in rows:
        if r.state != "open" or r.severity not in SEVERITIES:
            continue
        entry = repos[r.repo]
        entry["open"][r.severity] += 1
        age = _age_days(r.created_at, now)
        sla = _window(r, sla_days, by_kind)
        breached = sla is not None and age > sla
        entry["breaches"] += breached
        # The "oldest" alert is the link target, preferring breaching ones.
        key = (breached, age)
        if entry["oldest"] is None or key > entry["oldest"][0]:
            entry["oldest"] = (key, r.number, age)
    result = [
        {
            "repo": repo,
            "open": e["open"],
            "breaches": e["breaches"],
            "oldest_age_days": round(e["oldest"][2], 1),
            "oldest_alert_number": e["oldest"][1],
        }
        for repo, e in repos.items()
    ]
    result.sort(key=lambda r: (-r["breaches"], -r["oldest_age_days"], r["repo"]))
    return result[:_MAX_REPO_ROWS]


def trend(rows: list, today: date, days: int) -> list[dict]:
    """Open alerts per severity at the end of each of the last `days` days.

    One pass over the alerts: an alert counts from the day it opened up to (not including) the day
    it closed, clipped to the window, recorded as +1/-1 in a per-severity difference array.
    """
    start = today - timedelta(days=days - 1)
    diff = {sev: [0] * (days + 1) for sev in SEVERITIES}
    for r in rows:
        if r.severity not in diff:
            continue
        lo = max((r.created_at.astimezone(timezone.utc).date() - start).days, 0)
        closed = _closed_at(r)
        hi = days if closed is None else min((closed.astimezone(timezone.utc).date() - start).days, days)
        if lo < hi:
            diff[r.severity][lo] += 1
            diff[r.severity][hi] -= 1
    out = []
    running = dict.fromkeys(SEVERITIES, 0)
    for i in range(days):
        for sev in SEVERITIES:
            running[sev] += diff[sev][i]
        out.append({"date": (start + timedelta(days=i)).isoformat(), **running})
    return out


def compute(rows: list, now: datetime, days: int, sla_days: dict[str, int | None], by_kind=None) -> dict:
    days = max(1, min(days, TREND_MAX_DAYS))
    return {
        "window_days": days,
        "severities": summarize(rows, now, sla_days, by_kind),
        "repos": by_repo(rows, now, sla_days, by_kind),
        "trend": trend(rows, now.date(), days),
    }
