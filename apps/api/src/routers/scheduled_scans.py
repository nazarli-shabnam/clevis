"""Scheduled scans: the org's on/off switch, and "what changed since the previous scan".

Both read data a scan already stored. ``/analytics/changes`` compares the org's two latest scans check by
check, so it works the same whether a person, CI or the scheduler ran them.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import ScanResult, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, scan_results_repo
from src.schemas.analytics import (
    CheckChange,
    ScanChangesResponse,
    ScheduledScanSettings,
    ScheduledScanUpdate,
)
from src.services.scan_schedule import effective_cadence, instance_cadence
from src.services.scan_service import comparable_scans

router = APIRouter()

_FAILING = {"fail", "error"}


def _settings(org) -> ScheduledScanSettings:
    instance = instance_cadence()
    cadence = effective_cadence(org.scheduled_scans, instance)
    return ScheduledScanSettings(
        enabled=org.scheduled_scans, effective=cadence is not None, cadence=cadence, instance_cadence=instance
    )


@router.get("/orgs/{org_login}/scheduled-scans", response_model=ScheduledScanSettings)
def get_scheduled_scans(ctx: OrgContext = Depends(require_org_role(min_role="admin"))):
    return _settings(ctx.org)


@router.put("/orgs/{org_login}/scheduled-scans", response_model=ScheduledScanSettings)
def set_scheduled_scans(
    body: ScheduledScanUpdate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if ctx.org.scheduled_scans != body.enabled:
        previous = ctx.org.scheduled_scans
        ctx.org.scheduled_scans = body.enabled
        audit_repo.write(
            db,
            actor=user.email,
            action="scheduled_scans.updated",
            target=ctx.org.github_login,
            payload={"enabled": body.enabled, "previous": previous},
            tenant_id=ctx.org.tenant_id,
            commit=False,
        )
        db.commit()
    return _settings(ctx.org)


def _classify(old: dict | None, new: dict) -> str | None:
    """How one check moved between two scans; None when it did not change in a way worth showing."""
    was_failing = old is not None and old.get("status") in _FAILING
    is_failing = new.get("status") in _FAILING
    if is_failing and not was_failing:
        return "newly_failing"
    if is_failing and was_failing:
        return "still_failing"
    if was_failing and new.get("status") == "pass":
        return "newly_passing"
    return None


def _repos_of(check: dict) -> list[str]:
    value = check.get("value")
    repos = value.get("repos") if isinstance(value, dict) else None
    return [r for r in repos if isinstance(r, str)] if isinstance(repos, list) else []


@router.get("/orgs/{org_login}/analytics/changes", response_model=ScanChangesResponse)
def org_scan_changes(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    """The org's latest scan compared with the one before it."""
    rows = (
        db.query(ScanResult)
        .filter(func.lower(ScanResult.owner) == org_login.lower(), ScanResult.tenant_id == ctx.org.tenant_id)
        .order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
        .limit(2)
        .all()
    )
    if not rows:
        return ScanChangesResponse(org=ctx.org.github_login, has_previous=False)
    latest = rows[0]
    if len(rows) == 1:
        return ScanChangesResponse(
            org=ctx.org.github_login, has_previous=False, scanned_at=latest.created_at, score=latest.score
        )
    previous = rows[1]
    new_checks = scan_results_repo._parse_checks(latest.checks_json)
    old_checks = scan_results_repo._parse_checks(previous.checks_json)
    old_by_id = {c.get("id"): c for c in old_checks}

    changes: list[CheckChange] = []
    for check in new_checks:
        kind = _classify(old_by_id.get(check.get("id")), check)
        if kind is None:
            continue
        old = old_by_id.get(check.get("id"))
        changes.append(
            CheckChange(
                id=str(check.get("id")),
                title=str(check.get("title") or check.get("id")),
                severity=str(check.get("severity") or ""),
                change=kind,
                previous_status=old.get("status") if old else None,
                status=str(check.get("status")),
                repos=_repos_of(check),
            )
        )
    order = {"newly_failing": 0, "still_failing": 1, "newly_passing": 2}
    changes.sort(key=lambda c: (order[c.change], c.title))
    return ScanChangesResponse(
        org=ctx.org.github_login,
        has_previous=True,
        previous_scanned_at=previous.created_at,
        scanned_at=latest.created_at,
        previous_score=previous.score,
        score=latest.score,
        comparable=comparable_scans({"checks": old_checks}, {"checks": new_checks}),
        changes=changes,
    )
