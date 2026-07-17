import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.schemas.repos import (
    RepoListInput,
    RepoListResponse,
    RepoPullsInput,
    RepoPullsResponse,
    RepoStatsInput,
    RepoStatsResponse,
)
from src.services.github_client import GitHubClient
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

_STATS_CACHE_TTL_SECONDS = 600
_stats_cache: dict[tuple[str, str], tuple[float, RepoStatsResponse]] = {}


def _github_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        return HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    if isinstance(exc, httpx.RequestError):
        return HTTPException(status_code=503, detail="GitHub API unreachable")
    raise exc


def _client_token(payload: RepoListInput | RepoStatsInput | RepoPullsInput) -> str | None:
    return payload.token.get_secret_value() if payload.token else None


def _list_repos(owner: str, token: str) -> RepoListResponse:
    try:
        client = GitHubClient(token)
        repos = client.request_paginated(f"/orgs/{owner}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    return {"org": owner, "total": len(repos), "repos": repos}


def _fetch_stats(owner: str, repo: str, token: str) -> RepoStatsResponse:
    client = GitHubClient(token)
    commit_activity = client.request("GET", f"/repos/{owner}/{repo}/stats/commit_activity")
    participation = client.request("GET", f"/repos/{owner}/{repo}/stats/participation")
    contributors = client.request("GET", f"/repos/{owner}/{repo}/stats/contributors")
    return {
        "repository": f"{owner}/{repo}",
        # GitHub computes these stats asynchronously and returns 202 with an empty body
        # on a cache miss — treat "not a list/dict yet" as "not ready", not an error.
        "commit_activity": commit_activity if isinstance(commit_activity, list) else [],
        "participation": participation if isinstance(participation, dict) else {},
        "contributors": contributors if isinstance(contributors, list) else [],
    }


def _cached_stats(owner: str, repo: str, token: str) -> RepoStatsResponse:
    key = (owner, repo)
    now = time.monotonic()
    cached = _stats_cache.get(key)
    if cached and now - cached[0] < _STATS_CACHE_TTL_SECONDS:
        return cached[1]
    stats = _fetch_stats(owner, repo, token)
    _stats_cache[key] = (now, stats)
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
