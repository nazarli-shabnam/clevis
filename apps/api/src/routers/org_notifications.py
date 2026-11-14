"""In-app notification center for one org: what changed that a person opening Clevis should see.

Nothing is stored per notification. The feed is derived on demand, tenant-scoped, from data Clevis
already keeps: new critical Dependabot alerts (``security_alerts``), score drops between comparable
scans (``scan_results``), and, for org admins only, failed background jobs (``jobs``) and GitHub App
permission drift (``github_installations``). The one piece of stored state is each user's "read up
to" time (``notification_reads``) and the individual items they dismissed (``notification_dismissals``), so
read status follows them across devices.
"""

import re
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from src.core._sanitize import sanitize_error
from src.core.auth import UserOut, require_auth
from src.core.db import ApiToken, CheckWaiver, Job, ScanResult, SecurityAlert, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import (
    api_token_repo,
    installation_repo,
    notification_dismissal_repo,
    notification_preference_repo,
    notification_read_repo,
    scan_results_repo,
    waiver_repo,
)
from src.schemas.notification_feed import NotificationFeed, NotificationItem, NotificationPreferences
from src.services import app_permissions, dependabot_burndown
from src.services.scan_service import comparable_scans

router = APIRouter()

# Older changes are history, not notifications. The bell uses these defaults; the full notifications
# page asks for a longer window and more items (up to the caps) through ``days`` and ``limit``.
WINDOW_DAYS = 14
MAX_WINDOW_DAYS = 90
MAX_ITEMS = 50
MAX_LIMIT = 200
_PER_SOURCE = 40
# The stable ids the feed hands out: a kind plus the source row's integer id.
_MAX_SCANS_IN_WINDOW = 1000
_ITEM_ID = re.compile(r"^(critical_alert|score_drop|job_failed|permission_drift|waiver_expired|sla_breach|api_token_expiring):[0-9]{1,18}$")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# The feed is ordered by (at, kind, row id) descending. The cursor is the last item already loaded, so rows
# that share its timestamp are neither skipped nor repeated on the next page.
Cursor = tuple[datetime, str, int]


def _item_key(item: dict) -> Cursor:
    """The (time, kind, row id) sort key of a feed item, the same order the cursor uses."""
    kind, _, row_id = item["id"].partition(":")
    return (_aware(item["at"]), kind, int(row_id))


def _after_cursor(at_col, id_col, kind: str, cursor: Cursor | None) -> list:
    """SQL conditions keeping the rows of ``kind`` that sort after ``cursor`` in the feed order."""
    if cursor is None:
        return []
    at, cursor_kind, cursor_id = cursor
    if kind == cursor_kind:
        return [or_(at_col < at, and_(at_col == at, id_col < cursor_id))]
    # At the same instant, kinds sort descending: a smaller kind comes after the cursor's kind.
    return [at_col <= at] if kind < cursor_kind else [at_col < at]


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


def _critical_alert_items(
    db: Session, tenant_id: int, since: datetime, per_source: int, cursor: Cursor | None = None
) -> list[dict]:
    """New critical Dependabot alerts after the cursor, newest first."""
    q = db.query(SecurityAlert).filter(
        *_critical_alert_filters(tenant_id, since),
        *_after_cursor(SecurityAlert.ingested_at, SecurityAlert.id, "critical_alert", cursor),
    )
    rows = q.order_by(SecurityAlert.ingested_at.desc(), SecurityAlert.id.desc()).limit(per_source).all()
    return [
        {
            "id": f"critical_alert:{r.id}",
            "kind": "critical_alert",
            "at": r.ingested_at,
            "title": f"New critical Dependabot alert in {r.repo}",
            # The worker stores GitHub's advisory under ``security_advisory`` (event_consumer._normalize_security_alert).
            "detail": str(((r.details or {}).get("security_advisory") or {}).get("summary") or ""),
            "href": f"/security/burndown?repo={quote(r.repo, safe='')}",
        }
        for r in rows
    ]


def _score_drop_items(
    db: Session, org_login: str, tenant_id: int, since: datetime, per_source: int, cursor: Cursor | None = None
) -> list[dict]:
    # Only the cheap columns are read for every scan; ``checks_json`` is loaded below for scans that
    # actually dropped, since that is the only place it is needed.
    """Score drops between comparable scans in the window, newest first, after the cursor."""
    q = db.query(
        ScanResult.id, ScanResult.score, ScanResult.created_at, ScanResult.failed_checks, ScanResult.total_checks
    ).filter(func.lower(ScanResult.owner) == org_login.lower(), ScanResult.tenant_id == tenant_id)
    if cursor is not None:
        # Ties on the cursor's instant are settled on the drops below; a drop is dated by its newer scan,
        # so each remaining pair is still compared with the scan before it.
        q = q.filter(ScanResult.created_at <= cursor[0])
    ordered = q.order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
    # Every scan in the window, so a drop is not hidden behind many non-dropping scans. One extra row past
    # the cap is the predecessor of the last one; when the window is exhausted instead, the newest scan
    # before it is.
    # ponytail: capped so an org scanning every minute can't load a quarter of scans; drops older than the cap need the cursor.
    rows = ordered.filter(ScanResult.created_at >= since).limit(_MAX_SCANS_IN_WINDOW + 1).all()
    if len(rows) <= _MAX_SCANS_IN_WINDOW:
        predecessor = ordered.filter(ScanResult.created_at < since).first()
        if predecessor is not None:
            rows.append(predecessor)
    # rows are newest first: each scan is compared with the one before it in time.
    dropped = [
        (newer, older)
        for newer, older in zip(rows, rows[1:])
        if _aware(newer.created_at) >= since
        and newer.score is not None
        and older.score is not None
        and newer.score < older.score
        and (cursor is None or (_aware(newer.created_at), "score_drop", newer.id) < cursor)
    ]
    if not dropped:
        return []
    ids = {r.id for pair in dropped for r in pair}
    checks = dict(db.query(ScanResult.id, ScanResult.checks_json).filter(ScanResult.id.in_(ids)).all())
    items: list[dict] = []
    for newer, older in dropped:
        previous = {"checks": scan_results_repo._parse_checks(checks[older.id])}
        current = {"checks": scan_results_repo._parse_checks(checks[newer.id])}
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
                # The Security page opens a stored scan from ?scan, compared with ?compare; ?owner names the org.
                "href": f"/security?scan={newer.id}&owner={quote(org_login, safe='')}&compare={older.id}",
            }
        )
        if len(items) == per_source:
            break
    return items


def _failed_job_items(
    db: Session, org_login: str, tenant_id: int, since: datetime, per_source: int, cursor: Cursor | None = None
) -> list[dict]:
    """Failed background jobs after the cursor, newest first."""
    q = db.query(Job).filter(
        *_failed_job_filters(tenant_id, since),
        *_after_cursor(Job.updated_at, Job.id, "job_failed", cursor),
    )
    rows = q.order_by(Job.updated_at.desc(), Job.id.desc()).limit(per_source).all()
    return [
        {
            "id": f"job_failed:{r.id}",
            "kind": "job_failed",
            "at": r.updated_at,
            "title": f"Background job failed: {r.job_type}",
            # The stored result is an error string; keep the line short, the activity log has the rest.
            "detail": sanitize_error(r.result or "")[:140],
            "href": f"/settings/org/{quote(org_login, safe='')}/activity?job={r.id}",
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


def _waiver_expired_items(db: Session, org_login: str, tenant_id: int, since: datetime) -> list[dict]:
    """Waivers that lapsed inside the window: the check counts toward the score again from the next scan."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": f"waiver_expired:{w.id}",
            "kind": "waiver_expired",
            "at": w.expires_at,
            "title": f"Waiver expired: {w.check_id}",
            "detail": f"Owner {w.owner}: {w.reason}"[:140],
            # The Waivers card lives on the Members page; ?waiver highlights this one.
            "href": f"/settings/org/{quote(org_login, safe='')}/members?waiver={w.id}",
        }
        for w in waiver_repo.list_for_tenant(db, tenant_id)
        if since <= _aware(w.expires_at) <= now
    ]


def _api_token_expiring_items(db: Session, org_login: str, tenant_id: int) -> list[dict]:
    """Active API tokens that expire within the warning window. Dated by the moment each entered the
    window (expiry minus WINDOW_DAYS), so marking the feed read clears it and it does not reappear;
    it is a standing warning, so it is not cut by the feed's ``days``. Gone once revoked or expired."""
    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for t in api_token_repo.list_for_tenant(db, tenant_id):
        if t.revoked_at is not None or t.expires_at is None:
            continue
        expires_at = _aware(t.expires_at)
        if not now < expires_at <= now + timedelta(days=WINDOW_DAYS):
            continue
        days_left = max(1, -(-int((expires_at - now).total_seconds()) // 86400))
        items.append(
            {
                "id": f"api_token_expiring:{t.id}",
                "kind": "api_token_expiring",
                "at": expires_at - timedelta(days=WINDOW_DAYS),
                "title": f"API token \"{t.name}\" expires in {days_left} day{'s' if days_left != 1 else ''}",
                "detail": "Rotate it before CI jobs using it start failing.",
                # The API tokens card lives on the Members page; ?token highlights this one.
                "href": f"/settings/org/{quote(org_login, safe='')}/members?token={t.id}",
            }
        )
    return items


def _sla_breach_items(
    db: Session, tenant_id: int, since: datetime, per_source: int, cursor: Cursor | None = None
) -> list[dict]:
    """Open Dependabot alerts that passed their SLA window inside the feed window, dated by the breach
    moment (opened + window), so each is one item that appears when it crosses the line."""
    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for severity, days in dependabot_burndown.sla_days().items():
        if days is None:
            continue
        window = timedelta(days=days)
        rows = (
            db.query(SecurityAlert)
            .filter(
                SecurityAlert.tenant_id == tenant_id,
                SecurityAlert.kind == "dependabot",
                SecurityAlert.state == "open",
                SecurityAlert.severity == severity,
                SecurityAlert.created_at >= since - window,
                SecurityAlert.created_at <= now - window,
                # Dated by the breach moment (opened + window), so the cursor applies to that, per severity.
                *_after_cursor(SecurityAlert.created_at + window, SecurityAlert.id, "sla_breach", cursor),
            )
            .order_by(SecurityAlert.created_at.desc(), SecurityAlert.id.desc())
            .limit(per_source)
            .all()
        )
        items += [
            {
                "id": f"sla_breach:{r.id}",
                "kind": "sla_breach",
                "at": _aware(r.created_at) + window,
                "title": f"{severity.capitalize()} Dependabot alert past its {days}-day SLA in {r.repo}",
                "detail": f"Alert #{r.number}",
                "href": f"/security/burndown?repo={quote(r.repo, safe='')}",
            }
            for r in rows
        ]
    return items


def _sla_unread_beyond_limit(db: Session, tenant_id: int, since: datetime, last_read, items: list, dismissed) -> int:
    """Unread SLA breaches the per-source LIMIT kept out of ``items``, with the same windows and dismissal rule."""
    now = datetime.now(timezone.utc)
    dismissed_ids = [int(d[len("sla_breach:"):]) for d in dismissed if d.startswith("sla_breach:")]
    total = 0
    for severity, days in dependabot_burndown.sla_days().items():
        if days is None:
            continue
        window = timedelta(days=days)
        q = db.query(func.count()).select_from(SecurityAlert).filter(
            SecurityAlert.tenant_id == tenant_id,
            SecurityAlert.kind == "dependabot",
            SecurityAlert.state == "open",
            SecurityAlert.severity == severity,
            SecurityAlert.created_at >= since - window,
            SecurityAlert.created_at <= now - window,
        )
        if dismissed_ids:
            q = q.filter(SecurityAlert.id.notin_(dismissed_ids))
        if last_read is not None:
            q = q.filter(SecurityAlert.created_at + window > last_read)
        total += q.scalar() or 0
    listed = sum(
        1
        for i in items
        if i.kind == "sla_breach" and i.id not in dismissed and (last_read is None or _aware(i.at) > _aware(last_read))
    )
    return max(0, total - listed)


def _unread_beyond_limit(db: Session, filters: list, at_column, last_read, items: list, kind: str, dismissed) -> int:
    """Unread rows of one source past the read cursor that the per-source LIMIT kept out of ``items``.

    Rows the user dismissed are left out of both sides, so a dismissed row the limit cut is not counted
    as unread and a dismissed row that is listed does not make a cut row look listed."""
    model = at_column.class_
    prefix = f"{kind}:"
    dismissed_ids = [int(d[len(prefix):]) for d in dismissed if d.startswith(prefix)]
    q = db.query(func.count()).select_from(model).filter(*filters)
    if dismissed_ids:
        q = q.filter(model.id.notin_(dismissed_ids))
    if last_read is not None:
        q = q.filter(at_column > last_read)
    past_cursor_total = q.scalar() or 0
    past_cursor_listed = sum(
        1
        for i in items
        if i.kind == kind and i.id not in dismissed and (last_read is None or _aware(i.at) > _aware(last_read))
    )
    return max(0, past_cursor_total - past_cursor_listed)


@router.get("/orgs/{org_login}/notifications", response_model=NotificationFeed)
def get_notifications(
    org_login: str,
    days: int = Query(default=WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS, description="How far back to look"),
    limit: int = Query(default=MAX_ITEMS, ge=1, le=MAX_LIMIT, description="Most items to return"),
    before: datetime | None = Query(
        default=None,
        description="Cursor: only items dated before this time (the `at` of the last item already loaded). "
        "`unread_count` is 0 on such a page; take it from the first page.",
    ),
    before_id: str | None = Query(
        default=None,
        pattern=_ITEM_ID.pattern,
        description="With `before`: the `id` of the last item already loaded, so items sharing its `at` "
        "are neither skipped nor repeated.",
    ),
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """The caller's notification feed for this org, newest first, paged with the `before`/`before_id` cursor."""
    tenant_id = ctx.org.tenant_id
    since = datetime.now(timezone.utc) - timedelta(days=days)
    if before_id is not None and before is None:
        raise HTTPException(status_code=422, detail="before_id needs before")
    # Without before_id the cursor is strict: an empty kind sorts before every real one, so nothing at
    # `before` itself is kept.
    cursor: Cursor | None = None
    if before is not None:
        cursor_kind, _, cursor_row = (before_id or ":0").partition(":")
        cursor = (_aware(before), cursor_kind, int(cursor_row))
    per_source = max(_PER_SOURCE, limit)
    last_read = notification_read_repo.get_last_read(db, user.id, tenant_id)
    dismissed = notification_dismissal_repo.get_dismissed(db, user.id, tenant_id)
    # Muted kinds are left out of the list and of the unread count here, once, so the bell badge and the
    # Overview "since last visit" card (both read this feed) always agree.
    muted = notification_preference_repo.get_muted(db, user.id, tenant_id)
    is_admin = ctx.membership.role == "admin"

    raw: list[dict] = []
    if "critical_alert" not in muted:
        raw += _critical_alert_items(db, tenant_id, since, per_source, cursor)
    if "score_drop" not in muted:
        raw += _score_drop_items(db, org_login, tenant_id, since, per_source, cursor)
    if is_admin and "job_failed" not in muted:
        raw += _failed_job_items(db, org_login, tenant_id, since, per_source, cursor)
    if is_admin and "permission_drift" not in muted:
        raw += _permission_drift_items(db, ctx, org_login, since)
    if is_admin and "waiver_expired" not in muted:
        raw += _waiver_expired_items(db, org_login, tenant_id, since)
    if is_admin and "api_token_expiring" not in muted:
        raw += _api_token_expiring_items(db, org_login, tenant_id)
    if "sla_breach" not in muted:
        raw += _sla_breach_items(db, tenant_id, since, per_source, cursor)

    if cursor is not None:
        # permission drift, waivers and token expiries are not row-limited, so they are cut here instead of in a query.
        raw = [i for i in raw if _item_key(i) < cursor]
    raw.sort(key=_item_key, reverse=True)
    items = [
        NotificationItem(
            **i,
            read=(i["id"] in dismissed and _aware(dismissed[i["id"]]) >= _aware(i["at"]))
            or (last_read is not None and _aware(i["at"]) <= _aware(last_read)),
        )
        for i in raw
    ]
    # Counted before the list is cut to MAX_ITEMS, and (for the sources that are cut to _PER_SOURCE
    # rows) including the unread rows that cut left out, so a long list never under-reports.
    unread_count = sum(1 for i in items if not i.read)
    if cursor is not None:
        # A later page lists older items only; the unread badge is the first page's job.
        return NotificationFeed(org=org_login, unread_count=0, items=items[:limit], last_read_at=last_read)
    if "critical_alert" not in muted:
        unread_count += _unread_beyond_limit(db, _critical_alert_filters(tenant_id, since), SecurityAlert.ingested_at, last_read, items, "critical_alert", dismissed)
    if is_admin and "job_failed" not in muted:
        unread_count += _unread_beyond_limit(db, _failed_job_filters(tenant_id, since), Job.updated_at, last_read, items, "job_failed", dismissed)
    if "sla_breach" not in muted:
        unread_count += _sla_unread_beyond_limit(db, tenant_id, since, last_read, items, dismissed)
    return NotificationFeed(
        org=org_login,
        unread_count=unread_count,
        items=items[:limit],
        last_read_at=last_read,
    )


@router.get("/orgs/{org_login}/notifications/preferences", response_model=NotificationPreferences)
def get_notification_preferences(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    return NotificationPreferences(muted_kinds=sorted(notification_preference_repo.get_muted(db, user.id, ctx.org.tenant_id)))


@router.put("/orgs/{org_login}/notifications/preferences", response_model=NotificationPreferences)
def set_notification_preferences(
    org_login: str,
    body: NotificationPreferences,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Replace the caller's muted kinds for this org. Per user: it never changes what anyone else sees."""
    stored = notification_preference_repo.set_muted(db, user.id, ctx.org.tenant_id, body.muted_kinds)
    return NotificationPreferences(muted_kinds=stored)


_MAX_ROW_ID = 2_147_483_647  # the source tables use 32-bit integer ids


def _item_exists(db: Session, ctx: OrgContext, org_login: str, kind: str, row_id: int) -> bool:
    """Whether ``kind:row_id`` names a row this caller's feed can show, so dismissals only ever hold real items
    (not arbitrary ids a client invents, or admin-only ones a member cannot see)."""
    if row_id > _MAX_ROW_ID:
        return False
    tenant_id = ctx.org.tenant_id
    is_admin = ctx.membership.role == "admin"
    if kind == "critical_alert":
        model = SecurityAlert
    elif kind == "score_drop":
        model = ScanResult
    elif not is_admin:
        return False
    elif kind == "job_failed":
        return db.query(Job.id).filter(Job.id == row_id, *_failed_job_filters_any_age(tenant_id)).first() is not None
    elif kind == "permission_drift":
        install = installation_repo.get_for_org(db, org_id=ctx.org.id, account_login=org_login)
        return install is not None and install.id == row_id
    elif kind == "api_token_expiring":
        model = ApiToken
    else:
        model = CheckWaiver
    return db.query(model.id).filter(model.id == row_id, model.tenant_id == tenant_id).first() is not None


def _failed_job_filters_any_age(tenant_id: int) -> list:
    return [Job.tenant_id == tenant_id, Job.status == "failed"]


@router.post("/orgs/{org_login}/notifications/{item_id}/dismiss", status_code=204)
def dismiss_notification(
    org_login: str,
    item_id: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Mark one notification read for the caller only, without moving the read-up-to marker. ``item_id`` is
    an id from the feed; anything else is rejected so the table only ever holds ids the feed can produce."""
    if not _ITEM_ID.match(item_id):
        raise HTTPException(status_code=422, detail="Not a notification id")
    kind, _, raw_id = item_id.partition(":")
    if not _item_exists(db, ctx, org_login, kind, int(raw_id)):
        raise HTTPException(status_code=404, detail="No such notification")
    notification_dismissal_repo.dismiss(db, user.id, ctx.org.tenant_id, item_id)


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
