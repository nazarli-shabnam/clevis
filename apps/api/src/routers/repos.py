import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from checks.github_checks import BranchProtectionEnabled, SecretScanningEnabled
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.path_params import Owner, Repo
from src.core.db import Org, RepoEventDailyCount, get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import installation_repo, org_repo, tenant_repo
from src.schemas.repos import (
    RepoListInput,
    RepoFlowMetricsResponse,
    RepoListResponse,
    RepoPullsInput,
    RepoPullsResponse,
    RepoSecurityInput,
    RepoSecurityResponse,
    RepoStatsInput,
    RepoStatsResponse,
)
from src.services import flow_metrics, pr_nudge
from src.services.analytics_service import get_account_type
from src.services.github_client import GitHubClient, github_error as _github_error, list_owner_repos
from src.services.token_resolution import (
    InsufficientOrgRole,
    NoGitHubTokenAvailable,
    resolve_org_token,
    resolve_owner_token,
)

router = APIRouter()

_STATS_CACHE_TTL_SECONDS = 600
_stats_cache: dict[tuple[int, str, str, str, bool], tuple[float, RepoStatsResponse]] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_token(payload: RepoListInput | RepoStatsInput | RepoPullsInput | RepoSecurityInput) -> str | None:
    return payload.token.get_secret_value() if payload.token else None


def _list_repos(owner: str, token: str, account_type: str = "Organization") -> RepoListResponse:
    try:
        repos = list_owner_repos(GitHubClient(token), owner, account_type)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    return {"org": owner, "total": len(repos), "repos": repos}


def _fetch_repo_meta(client: GitHubClient, owner: str, repo: str) -> dict:
    # Metadata enriches the response but isn't essential -- degrade to an empty dict on
    # failure instead of discarding stats that already succeeded.
    try:
        return client.request("GET", f"/repos/{owner}/{repo}")
    except (httpx.HTTPStatusError, httpx.RequestError):
        return {}


def _fetch_latest_release(client: GitHubClient, owner: str, repo: str) -> dict | None:
    # Same reasoning as _fetch_repo_meta: a missing/unreachable release is never fatal,
    # not just the common "no releases at all" 404 case.
    try:
        release = client.request("GET", f"/repos/{owner}/{repo}/releases/latest")
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None
    return {
        "tag_name": release["tag_name"],
        "published_at": release.get("published_at"),
        "html_url": release["html_url"],
    }


def _repo_org_connected(db: Session, org_id: int, account_login: str) -> bool:
    # Same installation_id-presence gate as src.routers.github's org_events --
    # repo_event_daily_counts is only ever populated for a tenant with a real
    # connected installation.
    installation = installation_repo.get_for_org(db, org_id=org_id, account_login=account_login)
    return installation is not None and installation.installation_id is not None


# Cache-key stand-in for an owner that is not a Clevis org the caller belongs to (a personal account, or an org
# reached with the caller's own token): live GitHub data only. Real tenant ids start at 1.
_NO_TENANT = 0


def _owner_token(db: Session, user: UserOut, owner: str, payload) -> str:
    """The token for the ``/me/repos/{owner}/...`` routes: the org installation for an org the caller belongs to,
    otherwise the caller's own (personal installation or pasted token); same role gate as the other ``/me/*`` routes."""
    try:
        return resolve_owner_token(db, user_id=user.id, owner=owner, client_token=_client_token(payload))
    except InsufficientOrgRole as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _member_org(db: Session, user_id: int, owner: str) -> Org | None:
    """The Clevis org named ``owner`` when the caller belongs to it, else None."""
    org = org_repo.get_by_login_ci(db, owner)
    if org is None:
        return None
    org = org_repo.ensure_tenant_linked(db, org)
    return org if tenant_repo.get_membership(db, org.tenant_id, user_id) is not None else None


def _repo_commit_activity_from_aggregate(db: Session, tenant_id: int, repo: str) -> list[dict]:
    """Same {week, total, days} shape GitHub's stats/commit_activity returns (52 weeks,
    oldest first, week = Unix seconds at that week's Sunday 00:00 UTC, days = 7 ints
    Sun-Sat) -- built from repo_event_daily_counts' push-event counts instead of GitHub's
    actual commit counts. This is an approximation (a push can carry multiple commits) --
    callers must set RepoStatsResponse.commit_activity_source="aggregate" accordingly."""
    # UTC, not the process-local date: RepoEventDailyCount.day is always the UTC
    # calendar day, so a local date here could misalign the week boundary.
    today = datetime.now(timezone.utc).date()
    # weekday(): Monday=0..Sunday=6; this finds the Sunday on/before `today`.
    current_week_start = today - timedelta(days=(today.weekday() + 1) % 7)
    oldest_week_start = current_week_start - timedelta(weeks=51)

    rows = (
        db.query(RepoEventDailyCount.day, RepoEventDailyCount.count)
        .filter(
            RepoEventDailyCount.tenant_id == tenant_id,
            RepoEventDailyCount.repo == repo,
            RepoEventDailyCount.event_type == "push",
            RepoEventDailyCount.day >= oldest_week_start,
        )
        .all()
    )
    counts_by_day = {row.day: row.count for row in rows}

    weeks = []
    for i in range(52):
        week_start = oldest_week_start + timedelta(weeks=i)
        days = [counts_by_day.get(week_start + timedelta(days=d), 0) for d in range(7)]
        week_epoch = int(datetime(week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc).timestamp())
        weeks.append({"week": week_epoch, "total": sum(days), "days": days})
    return weeks


def _fetch_stats(owner: str, repo: str, token: str, db: Session, tenant_id: int, connected: bool) -> RepoStatsResponse:
    client = GitHubClient(token)
    # The calls are independent — run them concurrently. commit_activity/participation/
    # contributors propagate real errors; repo_meta/latest_release degrade gracefully.
    # When `connected`, commit_activity is served from repo_event_daily_counts instead.
    with ThreadPoolExecutor(max_workers=5) as pool:
        repo_meta_f = pool.submit(_fetch_repo_meta, client, owner, repo)
        commit_activity_f = None if connected else pool.submit(
            client.request, "GET", f"/repos/{owner}/{repo}/stats/commit_activity"
        )
        participation_f = pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/participation")
        contributors_f = pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/contributors")
        latest_release_f = pool.submit(_fetch_latest_release, client, owner, repo)

        repo_meta = repo_meta_f.result()
        if connected:
            commit_activity = _repo_commit_activity_from_aggregate(db, tenant_id, f"{owner}/{repo}")
            commit_activity_source = "aggregate"
        else:
            raw_commit_activity = commit_activity_f.result()
            # GitHub computes these stats asynchronously and returns 202 with an empty
            # body on a cache miss — treat "not a list yet" as "not ready", not an error.
            commit_activity = raw_commit_activity if isinstance(raw_commit_activity, list) else []
            commit_activity_source = "github"
        participation = participation_f.result()
        contributors = contributors_f.result()
        latest_release = latest_release_f.result()

    return {
        "repository": f"{owner}/{repo}",
        "commit_activity": commit_activity,
        "commit_activity_source": commit_activity_source,
        "participation": participation if isinstance(participation, dict) else {},
        "contributors": contributors if isinstance(contributors, list) else [],
        "stargazers_count": repo_meta.get("stargazers_count", 0),
        "forks_count": repo_meta.get("forks_count", 0),
        "watchers_count": repo_meta.get("watchers_count", 0),
        "open_issues_count": repo_meta.get("open_issues_count", 0),
        "default_branch": repo_meta.get("default_branch", ""),
        "latest_release": latest_release,
    }


def _evict_expired_stats(now: float) -> None:
    # Installation tokens rotate hourly, adding a new key each time -- without this sweep
    # the dict grows without bound for a long-running instance.
    # Snapshot: other request threads insert while we sweep, and iterating a changing dict raises.
    expired = [key for key, (cached_at, _) in list(_stats_cache.items()) if now - cached_at >= _STATS_CACHE_TTL_SECONDS]
    for key in expired:
        _stats_cache.pop(key, None)


def _cached_stats(owner: str, repo: str, token: str, db: Session, tenant_id: int, connected: bool) -> RepoStatsResponse:
    # `connected` is part of the key: without it, an installation connecting/disconnecting
    # mid-TTL could serve a cached response with a stale commit_activity_source. `tenant_id`
    # is part of the key too, so cache correctness doesn't rely on (owner, repo) staying
    # unique across tenants.
    key = (tenant_id, owner, repo, _token_hash(token), connected)
    now = time.monotonic()
    cached = _stats_cache.get(key)
    if cached and now - cached[0] < _STATS_CACHE_TTL_SECONDS:
        return cached[1]
    stats = _fetch_stats(owner, repo, token, db, tenant_id, connected)
    _stats_cache[key] = (now, stats)
    _evict_expired_stats(now)
    return stats


def _list_pulls(owner: str, repo: str, token: str, state: str) -> RepoPullsResponse:
    try:
        client = GitHubClient(token)
        pulls = client.request_paginated(f"/repos/{owner}/{repo}/pulls", params={"state": state})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    summaries = [
        {
            "number": p["number"],
            "title": p["title"],
            "user": (p.get("user") or {}).get("login"),
            "created_at": p["created_at"],
            "html_url": p["html_url"],
            "updated_at": p.get("updated_at"),
            "draft": bool(p.get("draft")),
        }
        for p in pulls
    ]
    return {
        "repository": f"{owner}/{repo}",
        "total": len(summaries),
        "pulls": summaries,
        "stale_days": pr_nudge.configured_stale_days(),
    }


@router.post("/orgs/{org_login}/repos", response_model=RepoListResponse)
def org_list_repos(
    org_login: str,
    payload: RepoListInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=_client_token(payload))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _list_repos(org_login, token)


@router.post("/me/repos/{owner}", response_model=RepoListResponse)
def personal_list_repos(
    owner: Owner,
    payload: RepoListInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Repository list for any owner the caller may use, including a personal account that has no Clevis
    org row (``/orgs/{org}/repos`` needs one). Org members get the org installation as before; the same role
    gate as the other ``/me/*`` routes applies."""
    token = _owner_token(db, user, owner, payload)
    try:
        account_type = get_account_type(owner=owner, token=token)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    result = _list_repos(owner, token, account_type)
    if account_type == "User":
        # A pasted user token's /user/repos lists the token holder's repositories whatever `owner` was typed;
        # keep only the ones this owner really owns so another login is never reported with someone else's repos.
        repos = [r for r in result["repos"] if ((r.get("owner") or {}).get("login") or "").lower() == owner.lower()]
        result = {**result, "total": len(repos), "repos": repos}
    return result


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/stats", response_model=RepoStatsResponse)
def org_repo_stats(
    org_login: str,
    owner: Owner,
    repo: Repo,
    payload: RepoStatsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=owner, client_token=_client_token(payload))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    connected = _repo_org_connected(db, ctx.org.id, owner)
    try:
        return _cached_stats(owner, repo, token, db, ctx.org.tenant_id, connected)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


@router.post("/me/repos/{owner}/{repo}/stats", response_model=RepoStatsResponse)
def personal_repo_stats(
    owner: Owner,
    repo: Repo,
    payload: RepoStatsInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """``/orgs/{org}/repos/{owner}/{repo}/stats`` for any owner the caller may use, including a personal
    account that has no Clevis org row (that route answers 404 for one). An org the caller belongs to behaves
    exactly as before: its installation token and stored push-event aggregate."""
    token = _owner_token(db, user, owner, payload)
    org = _member_org(db, user.id, owner)
    connected = org is not None and _repo_org_connected(db, org.id, owner)
    try:
        return _cached_stats(owner, repo, token, db, org.tenant_id if org else _NO_TENANT, connected)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


_flow_cache: dict[tuple[int, str, str, str], tuple[float, dict]] = {}


def _cached_flow_metrics(tenant_id: int, owner: str, repo: str, token: str) -> dict:
    key = (tenant_id, owner, repo, _token_hash(token))
    now = time.monotonic()
    cached = _flow_cache.get(key)
    if cached and now - cached[0] < _STATS_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        result = flow_metrics.compute(GitHubClient(token), owner, repo)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    _flow_cache[key] = (now, result)
    # Same sweep as _stats_cache: rotating installation tokens add a key each hour.
    for stale in [k for k, (at, _) in list(_flow_cache.items()) if now - at >= _STATS_CACHE_TTL_SECONDS]:
        _flow_cache.pop(stale, None)
    return result


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/flow-metrics", response_model=RepoFlowMetricsResponse)
def org_repo_flow_metrics(
    org_login: str,
    owner: Owner,
    repo: Repo,
    payload: RepoStatsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=owner, client_token=_client_token(payload))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _cached_flow_metrics(ctx.org.tenant_id, owner, repo, token)


@router.post("/me/repos/{owner}/{repo}/flow-metrics", response_model=RepoFlowMetricsResponse)
def personal_repo_flow_metrics(
    owner: Owner,
    repo: Repo,
    payload: RepoStatsInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """``/orgs/{org}/repos/{owner}/{repo}/flow-metrics`` for any owner the caller may use (see personal_repo_stats)."""
    token = _owner_token(db, user, owner, payload)
    org = _member_org(db, user.id, owner)
    return _cached_flow_metrics(org.tenant_id if org else _NO_TENANT, owner, repo, token)


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/pulls", response_model=RepoPullsResponse)
def org_repo_pulls(
    org_login: str,
    owner: Owner,
    repo: Repo,
    payload: RepoPullsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=owner, client_token=_client_token(payload))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _list_pulls(owner, repo, token, payload.state)


@router.post("/me/repos/{owner}/{repo}/pulls", response_model=RepoPullsResponse)
def personal_repo_pulls(
    owner: Owner,
    repo: Repo,
    payload: RepoPullsInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """``/orgs/{org}/repos/{owner}/{repo}/pulls`` for any owner the caller may use (see personal_repo_stats)."""
    return _list_pulls(owner, repo, _owner_token(db, user, owner, payload), payload.state)


def _fetch_security(owner: str, repo: str, token: str) -> RepoSecurityResponse:
    client = GitHubClient(token)
    repo_meta = client.request("GET", f"/repos/{owner}/{repo}")

    # Reuse the existing org-wide checks, scoped to just this one repo — each check
    # loops over a `repos` list, so a length-1 list gives an exact single-repo answer.
    branch_result = BranchProtectionEnabled().run(
        owner=owner, token=token, base_url=client.base, repos=[repo_meta]
    )
    protected, unknown = branch_result["value"]["protected"], branch_result["value"]["unknown"]
    branch_protection = "unknown" if unknown else ("protected" if protected else "unprotected")

    # GitHub only includes `security_and_analysis` for tokens with admin access to the
    # repo; a lesser-privileged token has the key absent, which the check itself treats
    # as "disabled". Distinguish that here so the UI shows "unknown" instead.
    if repo_meta.get("security_and_analysis") is None:
        secret_scanning = "unknown"
    else:
        secret_result = SecretScanningEnabled().run(
            owner=owner, token=token, base_url=client.base, repos=[repo_meta]
        )
        secret_scanning = "enabled" if secret_result["value"]["enabled"] else "disabled"

    return {
        "repository": f"{owner}/{repo}",
        "branch_protection": branch_protection,
        "secret_scanning": secret_scanning,
    }


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/security", response_model=RepoSecurityResponse)
def org_repo_security(
    org_login: str,
    owner: Owner,
    repo: Repo,
    payload: RepoSecurityInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=owner, client_token=_client_token(payload))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return _fetch_security(owner, repo, token)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


@router.post("/me/repos/{owner}/{repo}/security", response_model=RepoSecurityResponse)
def personal_repo_security(
    owner: Owner,
    repo: Repo,
    payload: RepoSecurityInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """``/orgs/{org}/repos/{owner}/{repo}/security`` for any owner the caller may use (see personal_repo_stats)."""
    token = _owner_token(db, user, owner, payload)
    try:
        return _fetch_security(owner, repo, token)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
