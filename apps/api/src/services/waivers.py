"""Check waivers: accepted-risk exceptions that keep a failing check out of the score.

A waiver covers one check, for the whole org or for named repos, and always expires. While it is active
the check is stamped ``waived`` and ``scored=False`` (excluded from numerator and denominator, like
``not_applicable``) but stays in the scan, so nothing is hidden. Applying happens in one place,
``scan_service.persist_scan``, which every org scan path (session, API token, scheduled, a member's
personal scan) goes through, so the dashboard, badge, score API and alerts all see the same score.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from checks.github_checks import (
    BranchProtectionEnabled,
    CodeScanningCheck,
    DefaultBranchNoForcePushCheck,
    DependabotAlertsCheck,
    MAX_AFFECTED_REPOS,
    OrgMFARequired,
    SecretScanningEnabled,
)
from checks.hygiene_checks import HYGIENE_CHECKS

from src.core.db import CheckWaiver
from src.repositories import waiver_repo
from src.services.analytics_service import tally

# A waiver is a temporary exception, not a permanent opt-out.
MAX_WAIVER_DAYS = 90
MAX_WAIVERS_PER_TENANT = 100

KNOWN_CHECK_IDS = frozenset(
    cls.metadata.check_id
    for cls in (
        OrgMFARequired,
        BranchProtectionEnabled,
        SecretScanningEnabled,
        DependabotAlertsCheck,
        CodeScanningCheck,
        DefaultBranchNoForcePushCheck,
        *HYGIENE_CHECKS,
    )
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def max_expiry(now: datetime) -> datetime:
    return now + timedelta(days=MAX_WAIVER_DAYS)


def _stamp(waivers: list[CheckWaiver], until: datetime, repos: list[str] | None) -> dict:
    def distinct(values: list[str]) -> str:
        return "; ".join(dict.fromkeys(values))

    return {
        "waiver_ids": [w.id for w in waivers],
        "until": _aware(until).isoformat(),
        "reason": distinct([w.reason for w in waivers]),
        "owner": distinct([w.owner for w in waivers]),
        "repos": repos,
    }


def _waiver_for(check: dict, waivers: list[CheckWaiver]) -> dict | None:
    """The ``waived`` stamp for `check`, or None when no waiver covers what is failing.

    Only a check that is *failing* is waived. A passing one would shrink the denominator and make the
    score worse; an errored one means Clevis could not evaluate it (missing scope, rate limit), which an
    accepted-risk waiver must not hide, so it stays scored. A repo-scoped waiver needs the check's own
    list of failing repos, which is capped; if that list may be truncated, or the check failed without
    naming repos, the waiver does not apply rather than guess."""
    if check.get("status") != "fail":
        return None
    mine = [w for w in waivers if w.check_id == check.get("id")]
    org_wide = [w for w in mine if w.repos is None]
    if org_wide:
        best = max(org_wide, key=lambda w: _aware(w.expires_at))
        return _stamp([best], best.expires_at, None)
    failing = (check.get("value") or {}).get("repos") if isinstance(check.get("value"), dict) else None
    if not failing or len(failing) >= MAX_AFFECTED_REPOS:
        return None
    scoped = [w for w in mine if w.repos]
    covered = {r.lower() for w in scoped for r in w.repos}
    if not {str(r).lower() for r in failing} <= covered:
        return None
    used = [w for w in scoped if {r.lower() for r in w.repos} & {str(r).lower() for r in failing}]
    return _stamp(used, min(_aware(w.expires_at) for w in used), sorted(str(r) for r in failing))


def apply_to_result(db: Session, tenant_id: int, result: dict, now: datetime | None = None) -> None:
    """Stamp waived checks into a scan result and recompute its counts and score, in place."""
    now = now or datetime.now(timezone.utc)
    # RLS WITH CHECK/USING is strict equality on app.tenant_id, which the personal routes never set;
    # SET LOCAL to the tenant being scored is safe (same as scan_results_repo.insert).
    db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
    waivers = waiver_repo.active_for_tenant(db, tenant_id, now)
    if not waivers:
        return
    changed = False
    for check in result["checks"]:
        stamp = _waiver_for(check, waivers)
        if stamp is not None:
            check["waived"] = stamp
            check["scored"] = False
            changed = True
    if changed:
        result["total_checks"], result["failed_checks"], result["score"] = tally(result["checks"])
