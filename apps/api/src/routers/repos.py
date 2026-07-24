import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from checks.github_checks import BranchProtectionEnabled, SecretScanningEnabled
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.schemas.repos import (
    RepoListInput,
    RepoListResponse,
    RepoPullsInput,
    RepoPullsResponse,
    RepoSecurityInput,
    RepoSecurityResponse,
    RepoStatsInput,
    RepoStatsResponse,
)
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

_STATS_CACHE_TTL_SECONDS = 600
_stats_cache: dict[tuple[str, str, str], tuple[float, RepoStatsResponse]] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_token(payload: RepoListInput | RepoStatsInput | RepoPullsInput | RepoSecurityInput) -> str | None:
    return payload.token.get_secret_value() if payload.token else None


def _list_repos(owner: str, token: str) -> RepoListResponse:
    try:
        client = GitHubClient(token)
        repos = client.request_paginated(f"/orgs/{owner}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    return {"org": owner, "total": len(repos), "repos": repos}


def _fetch_repo_meta(client: GitHubClient, owner: str, repo: str) -> dict:
    # Metadata (stars/forks/etc.) enriches the response but isn't essential the way
    # commit_activity/participation/contributors are — a transient failure here
    # shouldn't discard stats that already succeeded, so degrade to an empty dict
    # (fields below fall back to sensible defaults) instead of failing the request.
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


def _fetch_stats(owner: str, repo: str, token: str) -> RepoStatsResponse:
    client = GitHubClient(token)
    # The five calls are independent — run them concurrently rather than one at a time.
    # commit_activity/participation/contributors intentionally still propagate real
    # errors (a 4xx/5xx here means the repo/stats are genuinely unavailable), while
    # repo_meta/latest_release degrade gracefully — see their own functions.
    with ThreadPoolExecutor(max_workers=5) as pool:
        repo_meta_f = pool.submit(_fetch_repo_meta, client, owner, repo)
        commit_activity_f = pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/commit_activity")
        participation_f = pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/participation")
        contributors_f = pool.submit(client.request, "GET", f"/repos/{owner}/{repo}/stats/contributors")
        latest_release_f = pool.submit(_fetch_latest_release, client, owner, repo)

        repo_meta = repo_meta_f.result()
        commit_activity = commit_activity_f.result()
        participation = participation_f.result()
        contributors = contributors_f.result()
        latest_release = latest_release_f.result()

    return {
        "repository": f"{owner}/{repo}",
        # GitHub computes these stats asynchronously and returns 202 with an empty body
        # on a cache miss — treat "not a list/dict yet" as "not ready", not an error.
        "commit_activity": commit_activity if isinstance(commit_activity, list) else [],
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
    # Installation tokens rotate hourly, so every org x repo x hour of uptime adds a new
    # key that's never revisited once its token_hash goes stale -- without this sweep the
    # dict grows without bound for a long-running instance serving many orgs.
    expired = [key for key, (cached_at, _) in _stats_cache.items() if now - cached_at >= _STATS_CACHE_TTL_SECONDS]
    for key in expired:
        del _stats_cache[key]


def _cached_stats(owner: str, repo: str, token: str) -> RepoStatsResponse:
    key = (owner, repo, _token_hash(token))
    now = time.monotonic()
    cached = _stats_cache.get(key)
    if cached and now - cached[0] < _STATS_CACHE_TTL_SECONDS:
        return cached[1]
    stats = _fetch_stats(owner, repo, token)
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
        }
        for p in pulls
    ]
    return {"repository": f"{owner}/{repo}", "total": len(summaries), "pulls": summaries}


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


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/stats", response_model=RepoStatsResponse)
def org_repo_stats(
    org_login: str,
    owner: str,
    repo: str,
    payload: RepoStatsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=owner, client_token=_client_token(payload))
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return _cached_stats(owner, repo, token)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/pulls", response_model=RepoPullsResponse)
def org_repo_pulls(
    org_login: str,
    owner: str,
    repo: str,
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


def _fetch_security(owner: str, repo: str, token: str) -> RepoSecurityResponse:
    client = GitHubClient(token)
    repo_meta = client.request("GET", f"/repos/{owner}/{repo}")

    # Reuse the existing org-wide checks, scoped to just this one repo — each check
    # already loops over a `repos` list internally, so a length-1 list gives an exact
    # single-repo answer without touching packages/checks.
    branch_result = BranchProtectionEnabled().run(
        owner=owner, token=token, base_url=client.base, repos=[repo_meta]
    )
    protected, unknown = branch_result["value"]["protected"], branch_result["value"]["unknown"]
    branch_protection = "unknown" if unknown else ("protected" if protected else "unprotected")

    # GitHub only includes `security_and_analysis` on the repo payload for tokens with
    # admin access to the repo — for a lesser-privileged token the key is simply absent,
    # which the check itself treats identically to "explicitly disabled". Distinguish
    # that here so the UI shows "unknown" instead of a confidently-wrong "disabled".
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
    owner: str,
    repo: str,
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
