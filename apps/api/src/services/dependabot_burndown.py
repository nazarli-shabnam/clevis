"""Dependabot alert burn-down from ingested ``security_alerts`` rows: open alerts by severity over
time, median/oldest age, and SLA breaches per severity and per repo.

Limits worth knowing (surfaced to users in the UI): rows exist only for alerts delivered by
webhook (there is no backfill), and the table keeps no separate "closed at" timestamp, so a closed
alert's ``updated_at`` stands in for its close time. An alert reopened later is shown as open
for its whole life in the trend.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

SEVERITIES = ("critical", "high", "medium", "low")
TREND_MAX_DAYS = 90
_MAX_REPO_ROWS = 50


def _age_days(created: datetime, now: datetime) -> float:
    return max((now - created).total_seconds() / 86400, 0.0)


def _closed_at(row) -> datetime | None:
    return None if row.state == "open" else row.updated_at


def summarize(rows: list, now: datetime, sla_days: dict[str, int | None]) -> list[dict]:
    out = []
    for severity in SEVERITIES:
        ages = [_age_days(r.created_at, now) for r in rows if r.state == "open" and r.severity == severity]
        sla = sla_days.get(severity)
        out.append(
            {
                "severity": severity,
                "open": len(ages),
                "median_age_days": round(statistics.median(ages), 1) if ages else None,
                "oldest_age_days": round(max(ages), 1) if ages else None,
                "sla_days": sla,
                "breaches": sum(1 for a in ages if sla is not None and a > sla),
            }
        )
    return out


def by_repo(rows: list, now: datetime, sla_days: dict[str, int | None]) -> list[dict]:
    repos: dict[str, dict] = defaultdict(lambda: {"open": dict.fromkeys(SEVERITIES, 0), "oldest": None, "breaches": 0})
    for r in rows:
        if r.state != "open" or r.severity not in SEVERITIES:
            continue
        entry = repos[r.repo]
        entry["open"][r.severity] += 1
        age = _age_days(r.created_at, now)
        sla = sla_days.get(r.severity)
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


def compute(rows: list, now: datetime, days: int, sla_days: dict[str, int | None]) -> dict:
    days = max(1, min(days, TREND_MAX_DAYS))
    return {
        "window_days": days,
        "severities": summarize(rows, now, sla_days),
        "repos": by_repo(rows, now, sla_days),
        "trend": trend(rows, now.date(), days),
    }
