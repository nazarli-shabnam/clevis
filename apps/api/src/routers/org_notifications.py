"""In-app notification center for one org: what changed that a person opening Clevis should see.

Nothing is stored per notification. The feed is derived on demand, tenant-scoped, from data Clevis
already keeps: new critical Dependabot alerts (``security_alerts``), score drops between comparable
scans (``scan_results``), and, for org admins only, failed background jobs (``jobs``) and GitHub App
permission drift (``github_installations``). The one piece of stored state is each user's "read up
to" time (``notification_reads``), so read status follows them across devices.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core._sanitize import sanitize_error
from src.core.auth import UserOut, require_auth
from src.core.db import Job, ScanResult, SecurityAlert, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import installation_repo, notification_read_repo, scan_results_repo
from src.schemas.notification_feed import NotificationFeed, NotificationItem
from src.services import app_permissions
from src.services.scan_service import comparable_scans

router = APIRouter()

# Older changes are history, not notifications.
WINDOW_DAYS = 14
MAX_ITEMS = 50
_PER_SOURCE = 40
_SCANS_CONSIDERED = 30


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _critical_alert_filters(tenant_id: int, since: datetime) -> list:
    return [
        SecurityAlert.tenant_id == tenant_id,
        SecurityAlert.kind == "dependabot",
        SecurityAlert.severity == "critical",
        SecurityAlert.state == "open",
        SecurityAlert.ingested_at >= since,
    ]


def _failed_job_filters(tenant_id: int, since: datetime) -> list:
    return [Job.tenant_id == tenant_id, Job.status == "failed", Job.updated_at >= since]


def _critical_alert_items(db: Session, tenant_id: int, since: datetime) -> list[dict]:
    rows = (
        db.query(SecurityAlert)
        .filter(*_critical_alert_filters(tenant_id, since))
        .order_by(SecurityAlert.ingested_at.desc())
        .limit(_PER_SOURCE)
        .all()
    )
    return [
        {
            "id": f"critical_alert:{r.id}",
            "kind": "critical_alert",
            "at": r.ingested_at,
            "title": f"New critical Dependabot alert in {r.repo}",
            "detail": str((r.details or {}).get("summary") or ""),
            "href": "/security",
        }
        for r in rows
    ]


def _score_drop_items(db: Session, org_login: str, tenant_id: int, since: datetime) -> list[dict]:
    rows = (
        db.query(ScanResult)
        .filter(func.lower(ScanResult.owner) == org_login.lower(), ScanResult.tenant_id == tenant_id)
        .order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
        .limit(_SCANS_CONSIDERED)
        .all()
    )
    items: list[dict] = []
    # rows are newest first: each scan is compared with the one before it in time.
    for newer, older in zip(rows, rows[1:]):
        if _aware(newer.created_at) < since:
            break
        if newer.score >= older.score:
            continue
        previous = {"checks": scan_results_repo._parse_checks(older.checks_json)}
        current = {"checks": scan_results_repo._parse_checks(newer.checks_json)}
        # Same rule as the score-drop alert: an errored check or a different scored set is not a real drop.
        if not comparable_scans(previous, current):
            continue
        items.append(
            {
                "id": f"score_drop:{newer.id}",
                "kind": "score_drop",
                "at": newer.created_at,
                "title": f"Security score dropped from {older.score} to {newer.score}",
                "detail": f"{newer.failed_checks} of {newer.total_checks} checks failing",
                "href": "/security",
            }
        )
    return items[:_PER_SOURCE]


def _failed_job_items(db: Session, org_login: str, tenant_id: int, since: datetime) -> list[dict]:
    rows = (
        db.query(Job)
        .filter(*_failed_job_filters(tenant_id, since))
        .order_by(Job.updated_at.desc())
        .limit(_PER_SOURCE)
        .all()
    )
    return [
        {
            "id": f"job_failed:{r.id}",
            "kind": "job_failed",
            "at": r.updated_at,
            "title": f"Background job failed: {r.job_type}",
            # The stored result is an error string; keep the line short, the activity log has the rest.
            "detail": sanitize_error(r.result or "")[:140],
            "href": f"/settings/org/{org_login}/activity",
        }
        for r in rows
    ]


def _permission_drift_items(db: Session, ctx: OrgContext, org_login: str, since: datetime) -> list[dict]:
    install = installation_repo.get_for_org(db, org_id=ctx.org.id, account_login=org_login)
    changed_at = install.permissions_changed_at if install is not None else None
    if install is None or changed_at is None or _aware(changed_at) < since:
        return []
    blocked = app_permissions.blocked_features(install.granted_permissions)
    if not blocked:
        return []
    labels = ", ".join(b.label for b in blocked)
    return [
        {
            "id": f"permission_drift:{install.id}",
            "kind": "permission_drift",
            "at": changed_at,
            "title": f"{len(blocked)} automation{'s' if len(blocked) != 1 else ''} need extra GitHub access",
            "detail": labels,
            "href": "/settings",
        }
    ]


def _unread_beyond_limit(db: Session, filters: list, at_column, last_read, items: list, kind: str) -> int:
    """Unread rows of one source that the per-source LIMIT kept out of ``items``."""
    q = db.query(func.count()).select_from(at_column.class_).filter(*filters)
    if last_read is not None:
        q = q.filter(at_column > last_read)
    unread_total = q.scalar() or 0
    unread_listed = sum(1 for i in items if i.kind == kind and not i.read)
    return max(0, unread_total - unread_listed)


@router.get("/orgs/{org_login}/notifications", response_model=NotificationFeed)
def get_notifications(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    tenant_id = ctx.org.tenant_id
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    last_read = notification_read_repo.get_last_read(db, user.id, tenant_id)

    raw = _critical_alert_items(db, tenant_id, since) + _score_drop_items(db, org_login, tenant_id, since)
    if ctx.membership.role == "admin":
        raw += _failed_job_items(db, org_login, tenant_id, since) + _permission_drift_items(db, ctx, org_login, since)

    raw.sort(key=lambda i: _aware(i["at"]), reverse=True)
    items = [
        NotificationItem(**i, read=last_read is not None and _aware(i["at"]) <= _aware(last_read)) for i in raw
    ]
    # Counted before the list is cut to MAX_ITEMS, and (for the sources that are cut to _PER_SOURCE
    # rows) including the unread rows that cut left out, so a long list never under-reports.
    unread_count = sum(1 for i in items if not i.read)
    unread_count += _unread_beyond_limit(db, _critical_alert_filters(tenant_id, since), SecurityAlert.ingested_at, last_read, items, "critical_alert")
    if ctx.membership.role == "admin":
        unread_count += _unread_beyond_limit(db, _failed_job_filters(tenant_id, since), Job.updated_at, last_read, items, "job_failed")
    return NotificationFeed(
        org=org_login,
        unread_count=unread_count,
        items=items[:MAX_ITEMS],
        last_read_at=last_read,
    )


class MarkReadBody(BaseModel):
    # The newest item the client has shown. Only items up to here are marked read, so one that arrived
    # after the feed was loaded is not silently swallowed. Omitted = everything up to now.
    up_to: datetime | None = None


@router.post("/orgs/{org_login}/notifications/read", status_code=204)
def mark_notifications_read(
    org_login: str,
    body: MarkReadBody = Body(default_factory=MarkReadBody),
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Mark notifications read for the caller only, up to ``up_to`` (clamped to now)."""
    now = datetime.now(timezone.utc)
    at = min(_aware(body.up_to), now) if body.up_to else now
    notification_read_repo.mark_read(db, user.id, ctx.org.tenant_id, at)
