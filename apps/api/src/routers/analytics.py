import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import anyio
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.auth import UserOut, require_auth
from src.core.db import RepoEventDailyCount, get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role, set_tenant_session_context
from src.repositories import installation_repo, job_repo, org_repo, scan_results_repo, tenant_repo
from src.routers.github import _cached_events, _fetch_events_from_repo_events
from src.schemas.analytics import (
    ActionsUsageResponse,
    AnalyticsInput,
    AnalyticsResponse,
    AtRiskRepo,
    CockpitResponse,
    IssueSummary,
    MilestoneSummary,
    MyIssueListResponse,
    MyPrListResponse,
    MyViewResponse,
    OrgEventSummary,
    PRSummary,
    PrCycleTimeWeek,
    PrWeekBucket,
    RunSummaryLite,
    ScanExportResponse,
    ScanHistoryEntry,
)
from src.services.analytics_service import get_account_type, get_overview
from src.services.github_client import GitHubClient, github_error as _github_error, list_owner_repos
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token, resolve_owner_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Caps the per-repo fan-out (one GitHub call per repo) for large orgs.
_MAX_REPOS_FOR_AGGREGATES = 30
_CACHE_JOB_TYPE = "github.clear_actions_cache"


async def _run_overview(owner: str, token: str, account_type: str = "Organization") -> AnalyticsResponse:
    try:
        return await anyio.to_thread.run_sync(lambda: get_overview(owner=owner, token=token, account_type=account_type))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")
    except Exception:
        logger.exception("analytics_overview failed")
        raise HTTPException(status_code=500, detail="Internal error")


async def _get_account_type(owner: str, token: str) -> str:
    try:
        return await anyio.to_thread.run_sync(lambda: get_account_type(owner=owner, token=token))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")


def _persist_scan(db: Session, result: dict, tenant_id: int | None, scanned_by_user_id: int | None = None) -> None:
    scan_results_repo.insert(
        db,
        owner=result["owner"],
        score=result["score"],
        total_checks=result["total_checks"],
        failed_checks=result["failed_checks"],
        checks=result["checks"],
        tenant_id=tenant_id,
        scanned_by_user_id=scanned_by_user_id,
    )


def _user_history_scope(db: Session, user: UserOut, owner: str) -> str | None:
    """How much of `owner`'s scan history this user may read (a local DB read, so it needs its own gate).

    ``"all"``: workspace Org member or personal installation for that login. ``"own"``: only their
    own BYO-PAT scans (`scanned_by_user_id`). ``None``: no access.
    """
    org = org_repo.get_by_login(db, owner)
    if org is not None:
        org = org_repo.ensure_tenant_linked(db, org)
        if tenant_repo.get_membership(db, org.tenant_id, user.id) is not None:
            return "all"
    if installation_repo.get_for_user(db, owner_user_id=user.id, account_login=owner) is not None:
        return "all"
    if scan_results_repo.exists_for_user(db, owner=owner, user_id=user.id):
        return "own"
    return None


def _user_can_read_history(db: Session, user: UserOut, owner: str) -> bool:
    return _user_history_scope(db, user, owner) is not None


@router.post("/orgs/{org_login}/analytics/overview", response_model=AnalyticsResponse)
async def org_analytics_overview(
    payload: AnalyticsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(payload.owner, ctx)
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_org_token(db, org_id=ctx.org.id, account_login=payload.owner, client_token=client_token)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = await _run_overview(payload.owner, token)
    _persist_scan(db, result, tenant_id=ctx.org.tenant_id)
    return result


@router.post("/me/analytics/overview", response_model=AnalyticsResponse)
async def personal_analytics_overview(
    payload: AnalyticsInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_owner_token(db, user_id=user.id, owner=payload.owner, client_token=client_token)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    account_type = await _get_account_type(payload.owner, token)
    result = await _run_overview(payload.owner, token, account_type=account_type)
    # owner can be any account the user has a token for (BYO-token), so the scan is recorded under
    # the scanning user's personal tenant.
    personal_tenant = tenant_repo.ensure_personal_tenant(db, user.id)
    _persist_scan(db, result, tenant_id=personal_tenant.id, scanned_by_user_id=user.id)
    return result


@router.get("/orgs/{org_login}/analytics/history", response_model=list[ScanHistoryEntry])
def org_analytics_history(
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    return scan_results_repo.list_recent(db, owner=ctx.org.github_login, limit=30)


def _billing_num(value: object) -> float:
    """Coerce a GitHub billing quantity to float; missing/non-numeric becomes 0.0 so shape drift can't 500."""
    return float(value) if isinstance(value, (int, float)) else 0.0


@router.get("/orgs/{org_login}/usage/actions", response_model=ActionsUsageResponse)
def org_actions_usage(
    response: Response,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    """GitHub Actions minutes used this billing month for the org.

    Needs the org Administration (read) permission, which Clevis doesn't request by default; a
    GitHub 403 becomes a 400 with a hint so the UI can hide the card. Served ``Cache-Control:
    no-store`` since billing data must never sit in a shared/browser cache (CWE-525).
    """
    try:
        token = resolve_org_token(
            db, org_id=ctx.org.id, account_login=ctx.org.github_login, client_token=x_github_token
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    try:
        data = GitHubClient(token).request(
            "GET",
            f"/organizations/{ctx.org.github_login}/settings/billing/usage/summary",
            params={"year": now.year, "month": now.month, "product": "actions"},
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Clevis's GitHub App can't read this org's Actions billing — grant it "
                    "the org 'Administration' (plan) permission. See docs/self-hosting.md."
                ),
            ) from exc
        raise _github_error(exc) from exc
    except httpx.RequestError as exc:
        raise _github_error(exc) from exc

    if not isinstance(data, dict) or not isinstance(data.get("usageItems"), list):
        raise HTTPException(status_code=502, detail="Unexpected response from GitHub billing API")

    total = included = paid = 0.0
    breakdown: dict[str, float] = {}
    for item in data["usageItems"]:
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail="Unexpected response from GitHub billing API")
        # The summary also carries Actions storage (unitType "GB"); count only minutes. GitHub's
        # unit-type casing isn't contractually fixed, so match loosely.
        if str(item.get("unitType", "")).lower() != "minutes":
            continue
        gross = _billing_num(item.get("grossQuantity"))
        total += gross
        included += _billing_num(item.get("discountQuantity"))
        paid += _billing_num(item.get("netQuantity"))
        sku = str(item.get("sku") or "actions")
        breakdown[sku] = breakdown.get(sku, 0.0) + gross

    response.headers["Cache-Control"] = "no-store"
    return ActionsUsageResponse(
        total_minutes_used=total,
        included_minutes_used=included,
        paid_minutes_used=paid,
        minutes_used_breakdown=breakdown,
    )


@router.get("/me/analytics/history", response_model=list[ScanHistoryEntry])
def personal_analytics_history(
    owner: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if not _user_can_read_history(db, user, owner):
        raise HTTPException(status_code=403, detail="You don't have access to this owner's scan history")
    return scan_results_repo.list_recent(db, owner=owner, limit=30)


# Compliance export: scan history with per-check breakdown over an optional [since, until] window.
# Same access gating as the history endpoints; CSV is rendered client-side.

_EXPORT_MAX_ROWS = 5000


def _export_window(since: date | None, until: date | None) -> tuple[datetime | None, datetime | None]:
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=422, detail="`since` must not be after `until`")
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc) if since else None
    # `until` is an inclusive calendar day: widen to end of day.
    until_dt = (
        datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc) if until else None
    )
    return since_dt, until_dt


def _build_export_response(rows: list[dict], limit: int) -> ScanExportResponse:
    # limit+1 distinguishes a full page from a truncated one; an export must never be silently partial.
    truncated = len(rows) > limit
    entries = rows[:limit]
    return ScanExportResponse(truncated=truncated, row_count=len(entries), entries=entries)


@router.get("/orgs/{org_login}/analytics/export", response_model=ScanExportResponse)
def org_analytics_export(
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(_EXPORT_MAX_ROWS, ge=1, le=_EXPORT_MAX_ROWS),
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    since_dt, until_dt = _export_window(since, until)
    rows = scan_results_repo.list_for_export(
        db, owner=ctx.org.github_login, since=since_dt, until=until_dt, limit=limit
    )
    return _build_export_response(rows, limit)


@router.get("/me/analytics/export", response_model=ScanExportResponse)
def personal_analytics_export(
    owner: str,
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(_EXPORT_MAX_ROWS, ge=1, le=_EXPORT_MAX_ROWS),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    scope = _user_history_scope(db, user, owner)
    if scope is None:
        raise HTTPException(status_code=403, detail="You don't have access to this owner's scan history")
    since_dt, until_dt = _export_window(since, until)
    rows = scan_results_repo.list_for_export(
        db,
        owner=owner,
        since=since_dt,
        until=until_dt,
        limit=limit,
        # "own" scope: don't hand over scans other users (or an org) ran.
        scanned_by_user_id=user.id if scope == "own" else None,
    )
    return _build_export_response(rows, limit)


# Overview cockpit: DB reads plus independent GitHub calls. Every GitHub helper is best-effort
# (degrading the response) except _safe_list_repos, since nothing is computable without repos.


def _safe_list_repos(owner: str, token: str, account_type: str = "Organization") -> list[dict]:
    client = GitHubClient(token)
    return list_owner_repos(client, owner, account_type)


def _owner_search_qualifier(owner: str, account_type: str) -> str:
    # GitHub search needs `user:` rather than `org:` to scope to a personal account's repos.
    return f"user:{owner}" if account_type == "User" else f"org:{owner}"


def _safe_member_count(owner: str, token: str, account_type: str = "Organization") -> tuple[int | None, bool]:
    """Returns (count, ok). count is None for a User-type owner (no members concept; ok=True).

    ok=False means the call failed and count is a fallback 0; callers must fold it into `degraded`."""
    if account_type == "User":
        return None, True
    try:
        client = GitHubClient(token)
        return len(client.request_paginated(f"/orgs/{owner}/members")), True
    except (httpx.HTTPStatusError, httpx.RequestError):
        return 0, False


def _cockpit_connected_tenant(db: Session, user_id: int, owner: str) -> int | None:
    # The cockpit is a personal endpoint (require_auth only), so `owner` may be any login the caller
    # has a token for. Without this membership check any caller could read another org's aggregate
    # data by naming its login; a connected installation alone isn't authorization.
    org = org_repo.get_by_login_ci(db, owner)
    if org is None:
        return None
    org = org_repo.ensure_tenant_linked(db, org)
    if tenant_repo.get_membership(db, org.tenant_id, user_id) is None:
        return None
    installation = installation_repo.get_for_org(db, org_id=org.id, account_login=owner)
    if installation is None or installation.installation_id is None:
        return None
    # Set RLS context for the tenant-scoped aggregate reads below, now that membership is confirmed.
    # resolve_owner_token itself doesn't set it (known gap in that helper).
    set_tenant_session_context(db, org.tenant_id, user_id)
    return org.tenant_id


def _safe_recent_events(
    db: Session, owner: str, token: str, tenant_id: int | None
) -> tuple[list[OrgEventSummary], bool]:
    """Returns (events, ok); ok=False means the fetch failed and events is a fallback []."""
    try:
        if tenant_id is not None:
            events = _fetch_events_from_repo_events(db, owner, tenant_id, per_page=10).events
        else:
            events = _cached_events(owner, token, per_page=10).events
        return [OrgEventSummary(**e.model_dump()) for e in events[:5]], True
    except (httpx.HTTPStatusError, httpx.RequestError, HTTPException):
        return [], False


_ACTIVITY_STALE_HOURS_DEFAULT = 6


def _activity_stale_hours() -> int:
    # Same config key and clamp as gap_heal_sweep._read_stale_hours, so "stale" means the same thing.
    raw = get_config("gap_heal_stale_hours", str(_ACTIVITY_STALE_HOURS_DEFAULT))
    try:
        return max(1, min(168, int(raw)))
    except ValueError:
        return _ACTIVITY_STALE_HOURS_DEFAULT


def _recent_events_staleness(db: Session, tenant_id: int) -> bool:
    """True if this tenant's activity cursor is older than gap_heal_stale_hours or never synced.

    Lets the UI label stale data as stale. Tenant context is already set by _cockpit_connected_tenant."""
    row = db.execute(
        text("SELECT last_synced_at FROM activity_sync_cursors WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).fetchone()
    if row is None or row[0] is None:
        return True
    last_synced_at = row[0]
    if last_synced_at.tzinfo is None:
        last_synced_at = last_synced_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_activity_stale_hours())
    return last_synced_at < cutoff


def _cockpit_events_and_commit_activity(
    db: Session, owner: str, token: str, tenant_id: int | None, repo_names: list[str]
) -> tuple[list[OrgEventSummary], bool, bool, tuple[list[int], list[int], bool]]:
    # One call, not two gather entries: both branches read `db`, and a Session isn't safe to use
    # from two threads at once.
    recent_events, recent_events_ok = _safe_recent_events(db, owner, token, tenant_id)
    recent_events_stale = _recent_events_staleness(db, tenant_id) if tenant_id is not None else False
    if tenant_id is not None:
        commit_activity = (*_cockpit_commit_activity_from_aggregate(db, tenant_id), True)
    else:
        commit_activity = _safe_commit_activity_4w_and_heatmap_52w(owner, token, repo_names)
    return recent_events, not recent_events_ok, recent_events_stale, commit_activity


def _cockpit_commit_activity_from_aggregate(db: Session, tenant_id: int) -> tuple[list[int], list[int]]:
    """Same {4w, 52w} shape, approximated from repo_event_daily_counts' push counts across the tenant.

    Approximate since a push can carry multiple commits. Callers must set commit_activity_source="aggregate"."""
    today = datetime.now(timezone.utc).date()
    current_week_start = today - timedelta(days=(today.weekday() + 1) % 7)
    oldest_week_start = current_week_start - timedelta(weeks=51)

    rows = (
        db.query(RepoEventDailyCount.day, func.sum(RepoEventDailyCount.count))
        .filter(
            RepoEventDailyCount.tenant_id == tenant_id,
            RepoEventDailyCount.event_type == "push",
            RepoEventDailyCount.day >= oldest_week_start,
        )
        .group_by(RepoEventDailyCount.day)
        .all()
    )
    counts_by_day = {day: int(count) for day, count in rows}

    totals_52w = [
        sum(counts_by_day.get(oldest_week_start + timedelta(weeks=i, days=d), 0) for d in range(7))
        for i in range(52)
    ]
    return totals_52w[-4:], totals_52w


def _week_start(weeks_ago: int) -> date:
    today = date.today()
    start_of_this_week = today - timedelta(days=today.weekday())
    return start_of_this_week - timedelta(weeks=weeks_ago)


def _search_count(client: GitHubClient, query: str) -> int:
    result = client.request("GET", "/search/issues", params={"q": query, "per_page": 1})
    return result.get("total_count", 0) if isinstance(result, dict) else 0


def _safe_open_pr_count(owner: str, token: str, account_type: str = "Organization") -> tuple[int, bool]:
    try:
        client = GitHubClient(token)
        qualifier = _owner_search_qualifier(owner, account_type)
        return _search_count(client, f"{qualifier} type:pr state:open"), True
    except (httpx.HTTPStatusError, httpx.RequestError):
        return 0, False


def _safe_pr_merge_rate_4w(owner: str, token: str, account_type: str = "Organization") -> list[PrWeekBucket]:
    try:
        client = GitHubClient(token)
        qualifier = _owner_search_qualifier(owner, account_type)
        week_starts = [_week_start(weeks_ago) for weeks_ago in range(3, -1, -1)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                (
                    start,
                    pool.submit(_search_count, client, f"{qualifier} type:pr created:{start}..{start + timedelta(days=7)}"),
                    pool.submit(_search_count, client, f"{qualifier} type:pr merged:{start}..{start + timedelta(days=7)}"),
                )
                for start in week_starts
            ]
            return [
                PrWeekBucket(week=start.isoformat(), opened=opened_f.result(), merged=merged_f.result())
                for start, opened_f, merged_f in futures
            ]
    except (httpx.HTTPStatusError, httpx.RequestError):
        return []


def _week_total(week: dict) -> int:
    """Return a commit_activity week's total; raise AttributeError/TypeError on a malformed record.

    A malformed record must not coerce to 0 (looks like a real zero week). Rejects bool (an int
    subclass), fractional and negative counts, which list[int] can't represent faithfully."""
    total = week.get("total", 0)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise TypeError(f"invalid week total: {total!r}")
    return total


def _safe_commit_activity_4w_and_heatmap_52w(
    owner: str, token: str, repo_names: list[str]
) -> tuple[list[int], list[int], bool]:
    # Both windows slice the same /stats/commit_activity response (52 weeks), so each repo is fetched once.
    # A failing repo is skipped and flips `ok`, so totals are an honest partial sum rather than "0 commits".
    client = GitHubClient(token)
    totals_4w = [0, 0, 0, 0]
    totals_52w = [0] * 52
    ok = True
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [
            pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/commit_activity")
            for repo in repo_names[:_MAX_REPOS_FOR_AGGREGATES]
        ]
        for future in futures:
            try:
                weeks = future.result()
            except (httpx.HTTPStatusError, httpx.RequestError):
                ok = False
                continue
            if not isinstance(weeks, list):
                ok = False
                continue
            # A malformed record must not escape asyncio.gather and fail the whole cockpit. Accumulate
            # into a local delta so a mid-repo failure can't half-apply across the two arrays.
            try:
                delta_4w = [_week_total(week) for week in weeks[-4:]] if len(weeks) >= 4 else [0, 0, 0, 0]
                delta_52w = [_week_total(week) for week in weeks[-52:]] if len(weeks) >= 52 else [0] * 52
            except (AttributeError, TypeError):
                ok = False
                continue
            for i in range(4):
                totals_4w[i] += delta_4w[i]
            for i in range(52):
                totals_52w[i] += delta_52w[i]
    return totals_4w, totals_52w, ok


def _cache_entry_bytes(entry: dict) -> int:
    """Same contract as _week_total: raise on a malformed, bool, negative or fractional size."""
    size = entry.get("size_in_bytes", 0)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise TypeError(f"invalid cache entry size: {size!r}")
    return size


def _safe_total_cache_bytes(owner: str, token: str, repo_names: list[str]) -> tuple[int, bool]:
    # Per-repo failures are caught in the loop, so no outer try/except.
    client = GitHubClient(token)
    total = 0
    ok = True
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [
            pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/actions/caches")
            for repo in repo_names[:_MAX_REPOS_FOR_AGGREGATES]
        ]
        for future in futures:
            try:
                data = future.result()
            except (httpx.HTTPStatusError, httpx.RequestError):
                ok = False
                continue
            if not isinstance(data, dict):
                ok = False
                continue
            # A malformed cache entry must degrade this one repo, not fail the whole cockpit.
            try:
                total += sum(_cache_entry_bytes(c) for c in data.get("actions_caches", []))
            except (AttributeError, TypeError):
                ok = False
    return total, ok


def _milestone_state(due_on: str | None, progress_pct: float) -> str:
    if not due_on:
        return "on_track"
    try:
        due = datetime.fromisoformat(due_on.replace("Z", "+00:00"))
    except ValueError:
        return "on_track"
    now = datetime.now(timezone.utc)
    if due < now:
        return "overdue"
    if due - now < timedelta(days=7) and progress_pct < 70:
        return "at_risk"
    return "on_track"


def _safe_milestones(owner: str, token: str, repo_names: list[str]) -> tuple[list[MilestoneSummary], list[AtRiskRepo]]:
    """Each repo's open milestones, best-effort per repo so one broken repo doesn't blank the rest."""
    client = GitHubClient(token)
    milestones: list[MilestoneSummary] = []

    def _fetch(repo: str) -> list[dict]:
        try:
            return client.request("GET", f"/repos/{owner}/{repo}/milestones", params={"state": "open"})
        except (httpx.HTTPStatusError, httpx.RequestError):
            return []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch, repo): repo for repo in repo_names[:_MAX_REPOS_FOR_AGGREGATES]}
        for future, repo in futures.items():
            for m in future.result():
                open_issues = m.get("open_issues", 0)
                closed_issues = m.get("closed_issues", 0)
                total = open_issues + closed_issues
                progress_pct = round((closed_issues / total) * 100, 1) if total else 0.0
                due_on = m.get("due_on")
                milestones.append(
                    MilestoneSummary(
                        repo=repo,
                        title=m.get("title", ""),
                        due_on=due_on,
                        open_issues=open_issues,
                        closed_issues=closed_issues,
                        progress_pct=progress_pct,
                        state=_milestone_state(due_on, progress_pct),
                    )
                )

    milestones.sort(key=lambda m: (m.due_on is None, m.due_on))

    at_risk_by_repo: dict[str, AtRiskRepo] = {}
    for m in milestones:
        if m.state == "on_track":
            continue
        severity = "critical" if m.state == "overdue" else "warning"
        reason = (
            f"Milestone '{m.title}' overdue"
            if m.state == "overdue"
            else f"Milestone '{m.title}' due soon at {m.progress_pct:.0f}% complete"
        )
        existing = at_risk_by_repo.get(m.repo)
        if existing is None:
            at_risk_by_repo[m.repo] = AtRiskRepo(repo=m.repo, reasons=[reason], severity=severity)
        else:
            existing.reasons.append(reason)
            if severity == "critical":
                existing.severity = "critical"

    at_risk_repos = sorted(at_risk_by_repo.values(), key=lambda r: r.severity != "critical")
    return milestones[:10], at_risk_repos[:10]


def _week_pr_cycle_time(client: GitHubClient, owner: str, start: date, account_type: str = "Organization") -> PrCycleTimeWeek:
    # closed_at approximates merge time (the search issues endpoint doesn't expose merged_at).
    # Search date qualifiers are inclusive at day granularity, so +6 days avoids double-counting.
    qualifier = _owner_search_qualifier(owner, account_type)
    result = client.request(
        "GET",
        "/search/issues",
        params={"q": f"{qualifier} type:pr merged:{start}..{start + timedelta(days=6)}", "per_page": 30},
    )
    items = result.get("items", []) if isinstance(result, dict) else []
    days: list[float] = []
    for item in items:
        try:
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            closed = datetime.fromisoformat(item["closed_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        days.append((closed - created).total_seconds() / 86400)
    avg_days = round(sum(days) / len(days), 1) if days else 0.0
    return PrCycleTimeWeek(week=start.isoformat(), avg_days=avg_days)


def _safe_pr_cycle_time_8w(owner: str, token: str, account_type: str = "Organization") -> list[PrCycleTimeWeek]:
    try:
        client = GitHubClient(token)
        week_starts = [_week_start(weeks_ago) for weeks_ago in range(7, -1, -1)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(_week_pr_cycle_time, client, owner, start, account_type) for start in week_starts
            ]
            return [f.result() for f in futures]
    except (httpx.HTTPStatusError, httpx.RequestError):
        return []


def _safe_release_cadence_4w(owner: str, token: str, repo_names: list[str]) -> list[int]:
    """Weekly release counts across the org's repos for the last 4 weeks."""
    week_starts = [_week_start(weeks_ago) for weeks_ago in range(3, -1, -1)]
    totals = [0, 0, 0, 0]

    def _fetch(repo: str) -> list[dict]:
        try:
            client = GitHubClient(token)
            return client.request("GET", f"/repos/{owner}/{repo}/releases", params={"per_page": 20})
        except (httpx.HTTPStatusError, httpx.RequestError):
            return []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_fetch, repo) for repo in repo_names[:_MAX_REPOS_FOR_AGGREGATES]]
        for future in futures:
            releases = future.result()
            if not isinstance(releases, list):
                continue
            for r in releases:
                published_at = r.get("published_at")
                if not published_at:
                    continue
                try:
                    published = datetime.fromisoformat(published_at.replace("Z", "+00:00")).date()
                except ValueError:
                    continue
                for i, start in enumerate(week_starts):
                    if start <= published < start + timedelta(days=7):
                        totals[i] += 1
                        break
    return totals


def _cache_job_success_rate(db: Session) -> float:
    jobs = job_repo.list_recent_by_type(db, job_type=_CACHE_JOB_TYPE, limit=20)
    done = sum(1 for j in jobs if j["status"] == "done")
    failed = sum(1 for j in jobs if j["status"] == "failed")
    return done / (done + failed) if (done + failed) else 0.0


@router.get("/me/analytics/cockpit/{owner}", response_model=CockpitResponse)
async def personal_analytics_cockpit(
    owner: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_owner_token(db, user_id=user.id, owner=owner, client_token=x_github_token)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Establish tenant context BEFORE any tenant-scoped read: scan_results is FORCE RLS, so reading it
    # first returns nothing under enforced RLS.
    connected_tenant_id = await anyio.to_thread.run_sync(lambda: _cockpit_connected_tenant(db, user.id, owner))

    # Scan history is a local DB read, so it needs its own access gate (same as /me/analytics/history).
    # A caller with no claim still gets the rest of the cockpit, just no trend.
    history_scope = await anyio.to_thread.run_sync(lambda: _user_history_scope(db, user, owner))
    scans = (
        scan_results_repo.list_recent(
            db,
            owner=owner,
            limit=10,
            scanned_by_user_id=user.id if history_scope == "own" else None,
        )
        if history_scope is not None
        else []
    )
    latest_score = scans[0]["score"] if scans else None
    score_trend = [s["score"] for s in reversed(scans)]
    cache_job_success_rate = _cache_job_success_rate(db)

    account_type = await _get_account_type(owner, token)
    try:
        repos = await anyio.to_thread.run_sync(lambda: _safe_list_repos(owner, token, account_type))
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    repo_names = [r["name"] for r in repos]

    (
        (member_count, member_count_ok),
        (recent_events, recent_events_degraded, recent_events_stale, (commit_activity_4w, commit_heatmap_52w, commit_activity_ok)),
        (open_pr_count, open_pr_count_ok),
        pr_merge_rate_4w,
        (total_cache_size_bytes, cache_bytes_ok),
        (milestones, at_risk_repos),
        pr_cycle_time_8w,
        release_cadence_4w,
    ) = await asyncio.gather(
        anyio.to_thread.run_sync(lambda: _safe_member_count(owner, token, account_type)),
        anyio.to_thread.run_sync(
            lambda: _cockpit_events_and_commit_activity(db, owner, token, connected_tenant_id, repo_names)
        ),
        anyio.to_thread.run_sync(lambda: _safe_open_pr_count(owner, token, account_type)),
        anyio.to_thread.run_sync(lambda: _safe_pr_merge_rate_4w(owner, token, account_type)),
        anyio.to_thread.run_sync(lambda: _safe_total_cache_bytes(owner, token, repo_names)),
        anyio.to_thread.run_sync(lambda: _safe_milestones(owner, token, repo_names)),
        anyio.to_thread.run_sync(lambda: _safe_pr_cycle_time_8w(owner, token, account_type)),
        anyio.to_thread.run_sync(lambda: _safe_release_cadence_4w(owner, token, repo_names)),
    )

    degraded = (
        not member_count_ok
        or not open_pr_count_ok
        or not cache_bytes_ok
        or not commit_activity_ok
        or recent_events_degraded
    )

    return CockpitResponse(
        repo_count=len(repos),
        member_count=member_count,
        latest_score=latest_score,
        score_trend=score_trend,
        recent_events=recent_events,
        open_pr_count=open_pr_count,
        pr_merge_rate_4w=pr_merge_rate_4w,
        commit_activity_4w=commit_activity_4w,
        commit_heatmap_52w=commit_heatmap_52w,
        total_cache_size_bytes=total_cache_size_bytes,
        cache_job_success_rate=cache_job_success_rate,
        at_risk_repos=at_risk_repos,
        milestones=milestones,
        pr_cycle_time_8w=pr_cycle_time_8w,
        release_cadence_4w=release_cadence_4w,
        commit_activity_source="aggregate" if connected_tenant_id is not None else "github",
        recent_events_source="aggregate" if connected_tenant_id is not None else "github",
        recent_events_stale=recent_events_stale,
        degraded=degraded,
    )


# My View: the token owner's open PRs, review queue, assigned issues and recent runs. Search spans
# every repo the token can see, so only token resolution is scoped to `owner`.

_MAX_REPOS_FOR_RUN_LOOKUP = 15


def _my_login(client: GitHubClient, fallback_login: str | None = None) -> str | None:
    try:
        data = client.request("GET", "/user")
        return data.get("login") if isinstance(data, dict) else None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            # A real auth/server failure (not the expected installation-token 403); propagate rather
            # than masquerade as zero PRs/issues.
            raise
        # Installation tokens get a 403 on /user; fall back to the Clevis user's OAuth-linked login.
        # None (empty response) if they never linked GitHub.
        return fallback_login


def _search_items(client: GitHubClient, query: str, per_page: int = 10) -> list[dict]:
    try:
        result = client.request("GET", "/search/issues", params={"q": query, "per_page": per_page})
        return result.get("items", []) if isinstance(result, dict) else []
    except (httpx.HTTPStatusError, httpx.RequestError):
        return []


def _pr_summaries(items: list[dict]) -> list[PRSummary]:
    return [
        PRSummary(
            number=i["number"],
            title=i.get("title", ""),
            repository=i.get("repository_url", "").split("/repos/")[-1],
            html_url=i.get("html_url", ""),
            updated_at=i["updated_at"],
        )
        for i in items
        if "number" in i and "updated_at" in i
    ]


def _issue_summaries(items: list[dict]) -> list[IssueSummary]:
    return [
        IssueSummary(
            number=i["number"],
            title=i.get("title", ""),
            repository=i.get("repository_url", "").split("/repos/")[-1],
            html_url=i.get("html_url", ""),
            updated_at=i["updated_at"],
        )
        for i in items
        if "number" in i and "updated_at" in i
    ]


def _safe_my_recent_runs(client: GitHubClient, owner: str, login: str, repo_names: list[str]) -> list[RunSummaryLite]:
    def _fetch(repo: str) -> list[dict]:
        try:
            data = client.request(
                "GET", f"/repos/{owner}/{repo}/actions/runs", params={"actor": login, "per_page": 5}
            )
            return data.get("workflow_runs", []) if isinstance(data, dict) else []
        except (httpx.HTTPStatusError, httpx.RequestError):
            return []

    runs: list[RunSummaryLite] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch, repo): repo for repo in repo_names[:_MAX_REPOS_FOR_RUN_LOOKUP]}
        for future, repo in futures.items():
            for r in future.result():
                runs.append(
                    RunSummaryLite(
                        repository=f"{owner}/{repo}",
                        id=r["id"],
                        name=r.get("name"),
                        status=r["status"],
                        conclusion=r.get("conclusion"),
                        html_url=r.get("html_url", ""),
                        created_at=r["created_at"],
                    )
                )
    runs.sort(key=lambda r: r.created_at, reverse=True)
    return runs[:10]


@router.get("/me/github/my-view", response_model=MyViewResponse)
async def my_view(
    owner: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_owner_token(db, user_id=user.id, owner=owner, client_token=x_github_token)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client = GitHubClient(token)
    login = await anyio.to_thread.run_sync(lambda: _my_login(client, user.github_login))
    if login is None:
        return MyViewResponse(identity_unresolved=True)

    account_type = await _get_account_type(owner, token)
    try:
        repos = await anyio.to_thread.run_sync(lambda: _safe_list_repos(owner, token, account_type))
    except (httpx.HTTPStatusError, httpx.RequestError):
        repos = []
    repo_names = [r["name"] for r in repos]

    (my_open_prs_raw, review_requests_raw, assigned_issues_raw, my_recent_runs) = await asyncio.gather(
        anyio.to_thread.run_sync(lambda: _search_items(client, f"is:pr is:open author:{login}")),
        anyio.to_thread.run_sync(lambda: _search_items(client, f"is:pr is:open review-requested:{login}")),
        anyio.to_thread.run_sync(lambda: _search_items(client, f"is:issue is:open assignee:{login}")),
        anyio.to_thread.run_sync(lambda: _safe_my_recent_runs(client, owner, login, repo_names)),
    )

    return MyViewResponse(
        my_open_prs=_pr_summaries(my_open_prs_raw),
        review_requests=_pr_summaries(review_requests_raw),
        assigned_issues=_issue_summaries(assigned_issues_raw),
        my_recent_runs=my_recent_runs,
    )


# My PRs / My Reviews / My Issues: paginated lists (via search total_count), separate from
# my_view's fixed top-10 widget.

# GitHub search only returns the first 1000 results; pages beyond that 422, so short-circuit to empty.
_MAX_SEARCH_RESULTS = 1000


def _search_items_page(client: GitHubClient, query: str, page: int, per_page: int) -> tuple[list[dict], int]:
    try:
        result = client.request("GET", "/search/issues", params={"q": query, "per_page": per_page, "page": page})
        if not isinstance(result, dict):
            return [], 0
        return result.get("items", []), result.get("total_count", 0)
    except (httpx.HTTPStatusError, httpx.RequestError):
        return [], 0


async def _my_items_list(
    db: Session,
    user: UserOut,
    owner: str,
    x_github_token: str | None,
    query_template: str,
    mapper,
    response_cls,
    page: int,
    per_page: int,
):
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_owner_token(db, user_id=user.id, owner=owner, client_token=x_github_token)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client = GitHubClient(token)
    login = await anyio.to_thread.run_sync(lambda: _my_login(client, user.github_login))
    if login is None:
        return response_cls(page=page, per_page=per_page, identity_unresolved=True)
    if page * per_page > _MAX_SEARCH_RESULTS:
        # Beyond the reachable window: report the capped total (not 0) so page math stays consistent.
        return response_cls(total_count=_MAX_SEARCH_RESULTS, page=page, per_page=per_page)

    query = query_template.format(login=login)
    items_raw, total_count = await anyio.to_thread.run_sync(lambda: _search_items_page(client, query, page, per_page))
    # Cap the reported total to what's reachable so the UI's Next button disables at the true boundary.
    return response_cls(
        items=mapper(items_raw), total_count=min(total_count, _MAX_SEARCH_RESULTS), page=page, per_page=per_page
    )


@router.get("/me/github/my-prs", response_model=MyPrListResponse)
async def my_prs(
    owner: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    return await _my_items_list(
        db, user, owner, x_github_token, "is:pr is:open author:{login}", _pr_summaries, MyPrListResponse, page, per_page
    )


@router.get("/me/github/my-reviews", response_model=MyPrListResponse)
async def my_reviews(
    owner: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    return await _my_items_list(
        db,
        user,
        owner,
        x_github_token,
        "is:pr is:open review-requested:{login}",
        _pr_summaries,
        MyPrListResponse,
        page,
        per_page,
    )


@router.get("/me/github/my-issues", response_model=MyIssueListResponse)
async def my_issues(
    owner: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    return await _my_items_list(
        db,
        user,
        owner,
        x_github_token,
        "is:issue is:open assignee:{login}",
        _issue_summaries,
        MyIssueListResponse,
        page,
        per_page,
    )
