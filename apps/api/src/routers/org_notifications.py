"""In-app notification center for one org: what changed that a person opening Clevis should see.

Nothing is stored per notification. The feed is derived on demand, tenant-scoped, from data Clevis
already keeps: new critical Dependabot alerts (``security_alerts``), score drops between comparable
scans (``scan_results``), and, for org admins only, failed background jobs (``jobs``) and GitHub App
permission drift (``github_installations``). The one piece of stored state is each user's "read up
to" time (``notification_reads``), so read status follows them across devices.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import Job, ScanResult, SecurityAlert, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import installation_repo, notification_read_repo, scan_results_repo
from src.routers.analytics import _comparable_scans
from src.schemas.notification_feed import NotificationFeed, NotificationItem
from src.services import app_permissions

router = APIRouter()

# Older changes are history, not notifications.
WINDOW_DAYS = 14
MAX_ITEMS = 50
_PER_SOURCE = 25
_SCANS_CONSIDERED = 30


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _critical_alert_items(db: Session, tenant_id: int, since: datetime) -> list[dict]:
    rows = (
        db.query(SecurityAlert)
        .filter(
            SecurityAlert.tenant_id == tenant_id,
            SecurityAlert.kind == "dependabot",
            SecurityAlert.severity == "critical",
            SecurityAlert.state == "open",
            SecurityAlert.created_at >= since,
        )
        .order_by(SecurityAlert.created_at.desc())
        .limit(_PER_SOURCE)
        .all()
    )
    return [
        {
            "id": f"critical_alert:{r.id}",
            "kind": "critical_alert",
            "at": r.created_at,
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
        if not _comparable_scans(previous, current):
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
        .filter(Job.tenant_id == tenant_id, Job.status == "failed", Job.updated_at >= since)
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
            "detail": (r.result or "")[:140],
            "href": f"/settings/org/{org_login}/activity",
        }
        for r in rows
    ]


def _permission_drift_items(db: Session, ctx: OrgContext, org_login: str) -> list[dict]:
    install = installation_repo.get_for_org(db, org_id=ctx.org.id, account_login=org_login)
    if install is None or install.permissions_synced_at is None:
        return []
    blocked = app_permissions.blocked_features(install.granted_permissions)
    if not blocked:
        return []
    labels = ", ".join(b.label for b in blocked)
    return [
        {
            "id": f"permission_drift:{install.id}",
            "kind": "permission_drift",
            "at": install.permissions_synced_at,
            "title": f"{len(blocked)} automation{'s' if len(blocked) != 1 else ''} need extra GitHub access",
            "detail": labels,
            "href": "/settings",
        }
    ]


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
        raw += _failed_job_items(db, org_login, tenant_id, since) + _permission_drift_items(db, ctx, org_login)

    raw.sort(key=lambda i: _aware(i["at"]), reverse=True)
    items = [
        NotificationItem(**i, read=last_read is not None and _aware(i["at"]) <= _aware(last_read)) for i in raw[:MAX_ITEMS]
    ]
    return NotificationFeed(
        org=org_login,
        items=items,
        unread_count=sum(1 for i in items if not i.read),
        last_read_at=last_read,
    )


@router.post("/orgs/{org_login}/notifications/read", status_code=204)
def mark_notifications_read(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Mark everything currently in the feed as read for the caller (only the caller's marker moves)."""
    notification_read_repo.mark_read(db, user.id, ctx.org.tenant_id, datetime.now(timezone.utc))
