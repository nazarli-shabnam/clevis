import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import anyio
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import installation_repo, job_repo, org_membership_repo, org_repo, scan_results_repo
from src.routers.github import _cached_events
from src.schemas.analytics import (
    AnalyticsInput,
    AnalyticsResponse,
    CockpitResponse,
    OrgEventSummary,
    PrWeekBucket,
    ScanHistoryEntry,
)
from src.services.analytics_service import get_account_type, get_overview
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token, resolve_personal_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Bounds the per-repo fan-out in _safe_commit_activity_4w / _safe_total_cache_bytes --
# each additional repo costs one more GitHub call, so large orgs are capped.
_MAX_REPOS_FOR_AGGREGATES = 30
_CACHE_JOB_TYPE = "github.clear_actions_cache"


async def _run_overview(owner: str, token: str) -> AnalyticsResponse:
    try:
        return await anyio.to_thread.run_sync(lambda: get_overview(owner=owner, token=token))
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


def _persist_scan(db: Session, result: dict, scanned_by_user_id: int | None = None) -> None:
    scan_results_repo.insert(
        db,
        owner=result["owner"],
        score=result["score"],
        total_checks=result["total_checks"],
        failed_checks=result["failed_checks"],
        checks=result["checks"],
        scanned_by_user_id=scanned_by_user_id,
    )


def _user_can_read_history(db: Session, user: UserOut, owner: str) -> bool:
    """Scan history isn't gated by GitHub-side authorization the way a live scan is
    (GitHub itself rejects a bad token/owner combo) -- it's a local DB read, so it
    needs its own access check. A user may read `owner`'s history if they're a
    member of the matching workspace Org, they personally have a GitHub App
    installation connected for that account login, or they're the one who ran a
    personal scan against that owner before (scanned_by_user_id, for owners with
    no workspace Org/membership at all -- the raw-PAT-paste flow)."""
    org = org_repo.get_by_login(db, owner)
    if org is not None and org_membership_repo.get(db, org.id, user.id) is not None:
        return True
    if installation_repo.get_for_user(db, owner_user_id=user.id, account_login=owner) is not None:
        return True
    return scan_results_repo.exists_for_user(db, owner=owner, user_id=user.id)


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
    _persist_scan(db, result)
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
            lambda: resolve_personal_token(
                db, owner_user_id=user.id, account_login=payload.owner, client_token=client_token
            )
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    account_type = await _get_account_type(payload.owner, token)
    if account_type == "User":
        raise HTTPException(
            status_code=422,
            detail="Personal GitHub accounts aren't supported for security scanning yet. Connect a GitHub organization instead.",
        )
    result = await _run_overview(payload.owner, token)
    _persist_scan(db, result, scanned_by_user_id=user.id)
    return result


@router.get("/orgs/{org_login}/analytics/history", response_model=list[ScanHistoryEntry])
def org_analytics_history(
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    return scan_results_repo.list_recent(db, owner=ctx.org.github_login, limit=30)


@router.get("/me/analytics/history", response_model=list[ScanHistoryEntry])
def personal_analytics_history(
    owner: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if not _user_can_read_history(db, user, owner):
        raise HTTPException(status_code=403, detail="You don't have access to this owner's scan history")
    return scan_results_repo.list_recent(db, owner=owner, limit=30)


# ---------------------------------------------------------------------------
# Overview cockpit (docs/plan.md Phase 12) -- aggregates DB reads plus several
# independent GitHub calls into one response. Each GitHub-calling helper below
# is best-effort except _safe_list_repos: a degraded cockpit (missing PR data
# because search was rate-limited, say) is more useful to an org-health
# dashboard than a 500/503 for the whole page, but nothing here is computable
# without at least the repo list, so that one call is allowed to fail hard.
# ---------------------------------------------------------------------------


def _safe_list_repos(owner: str, token: str) -> list[dict]:
    client = GitHubClient(token)
    return client.request_paginated(f"/orgs/{owner}/repos", params={"type": "all", "sort": "pushed"})


def _safe_member_count(owner: str, token: str) -> int:
    try:
        client = GitHubClient(token)
        return len(client.request_paginated(f"/orgs/{owner}/members"))
    except (httpx.HTTPStatusError, httpx.RequestError):
        return 0


def _safe_recent_events(owner: str, token: str) -> list[OrgEventSummary]:
    try:
        events = _cached_events(owner, token, per_page=10).events
        return [OrgEventSummary(**e.model_dump()) for e in events[:5]]
    except (httpx.HTTPStatusError, httpx.RequestError, HTTPException):
        return []


def _week_start(weeks_ago: int) -> date:
    today = date.today()
    start_of_this_week = today - timedelta(days=today.weekday())
    return start_of_this_week - timedelta(weeks=weeks_ago)


def _search_count(client: GitHubClient, query: str) -> int:
    result = client.request("GET", "/search/issues", params={"q": query, "per_page": 1})
    return result.get("total_count", 0) if isinstance(result, dict) else 0


def _safe_open_pr_count(owner: str, token: str) -> int:
    try:
        client = GitHubClient(token)
        return _search_count(client, f"org:{owner} type:pr state:open")
    except (httpx.HTTPStatusError, httpx.RequestError):
        return 0


def _safe_pr_merge_rate_4w(owner: str, token: str) -> list[PrWeekBucket]:
    try:
        client = GitHubClient(token)
        week_starts = [_week_start(weeks_ago) for weeks_ago in range(3, -1, -1)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                (
                    start,
                    pool.submit(_search_count, client, f"org:{owner} type:pr created:{start}..{start + timedelta(days=7)}"),
                    pool.submit(_search_count, client, f"org:{owner} type:pr merged:{start}..{start + timedelta(days=7)}"),
                )
                for start in week_starts
            ]
            return [
                PrWeekBucket(week=start.isoformat(), opened=opened_f.result(), merged=merged_f.result())
                for start, opened_f, merged_f in futures
            ]
    except (httpx.HTTPStatusError, httpx.RequestError):
        return []


def _safe_commit_activity_4w(owner: str, token: str, repo_names: list[str]) -> list[int]:
    # A single failing repo zeroes the whole aggregate rather than partially summing --
    # simpler than reconciling "which repos contributed" and consistent with this
    # function's own all-or-nothing best-effort contract to its caller.
    try:
        client = GitHubClient(token)
        totals = [0, 0, 0, 0]
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [
                pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/commit_activity")
                for repo in repo_names[:_MAX_REPOS_FOR_AGGREGATES]
            ]
            for future in futures:
                weeks = future.result()
                if isinstance(weeks, list) and len(weeks) >= 4:
                    for i, week in enumerate(weeks[-4:]):
                        totals[i] += week.get("total", 0)
        return totals
    except (httpx.HTTPStatusError, httpx.RequestError):
        return [0, 0, 0, 0]


def _safe_total_cache_bytes(owner: str, token: str, repo_names: list[str]) -> int:
    try:
        client = GitHubClient(token)
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [
                pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/actions/caches")
                for repo in repo_names[:_MAX_REPOS_FOR_AGGREGATES]
            ]
            results = [future.result() for future in futures]
            return sum(
                sum(c.get("size_in_bytes", 0) for c in data.get("actions_caches", []))
                for data in results
                if isinstance(data, dict)
            )
    except (httpx.HTTPStatusError, httpx.RequestError):
        return 0


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
            lambda: resolve_personal_token(
                db, owner_user_id=user.id, account_login=owner, client_token=x_github_token
            )
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    scans = scan_results_repo.list_recent(db, owner=owner, limit=10)
    latest_score = scans[0]["score"] if scans else None
    score_trend = [s["score"] for s in reversed(scans)]
    cache_job_success_rate = _cache_job_success_rate(db)

    try:
        repos = await anyio.to_thread.run_sync(lambda: _safe_list_repos(owner, token))
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    repo_names = [r["name"] for r in repos]

    (
        member_count,
        recent_events,
        open_pr_count,
        pr_merge_rate_4w,
        commit_activity_4w,
        total_cache_size_bytes,
    ) = await asyncio.gather(
        anyio.to_thread.run_sync(lambda: _safe_member_count(owner, token)),
        anyio.to_thread.run_sync(lambda: _safe_recent_events(owner, token)),
        anyio.to_thread.run_sync(lambda: _safe_open_pr_count(owner, token)),
        anyio.to_thread.run_sync(lambda: _safe_pr_merge_rate_4w(owner, token)),
        anyio.to_thread.run_sync(lambda: _safe_commit_activity_4w(owner, token, repo_names)),
        anyio.to_thread.run_sync(lambda: _safe_total_cache_bytes(owner, token, repo_names)),
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
        total_cache_size_bytes=total_cache_size_bytes,
        cache_job_success_rate=cache_job_success_rate,
    )
