"""GET-equivalent org activity feed — proxies GitHub's `/orgs/{org}/events` and
normalizes each raw event into a human-readable summary (docs/plan.md Phase 9).

Implemented as POST (not the literal GET the plan doc sketches) so an optional
client-supplied PAT travels in the request body, never a URL/query string --
matching every other GitHub-token-bearing endpoint in this codebase
(src.routers.repos's *Input models).

Mounted with the full "/github/orgs/{org_login}/events" path on the router
itself (no `prefix=` passed to include_router in main.py), matching every
sibling org-scoped router's convention of defining its complete path locally.
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.schemas.github import (
    FailedRunsInput,
    FailedRunsResponse,
    FailedRunSummary,
    OrgEvent,
    OrgEventsInput,
    OrgEventsResponse,
    ReleaseSummary,
    ReleaseTimelineInput,
    ReleaseTimelineResponse,
)
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

# Each repo costs one additional GitHub call for failed-runs/release-timeline, on
# top of the initial repo list -- same tier as security.py's per-repo matrix cap.
_MAX_REPOS_FOR_FEED = 20

# Short TTL, well under the frontend's 30s poll interval -- collapses concurrent
# polls from multiple open tabs/team members watching the same org into one
# upstream call, mirroring repos.py's _stats_cache for the same class of data
# (identical for every viewer of a given org at a given moment).
_EVENTS_CACHE_TTL_SECONDS = 25
_events_cache: dict[tuple[str, str, int], tuple[float, OrgEventsResponse]] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_bot(raw_event: dict) -> bool:
    login = (raw_event.get("actor") or {}).get("login", "")
    return login.endswith("[bot]")


def _summarize(raw_event: dict) -> str:
    event_type = raw_event.get("type", "")
    payload = raw_event.get("payload") or {}

    if event_type == "PushEvent":
        # GitHub truncates the embedded `commits` array to 20 entries even when
        # more were pushed -- `size` is the true total commit count.
        size = payload.get("size")
        commits = payload.get("commits") or []
        count = size if isinstance(size, int) else len(commits)
        branch = (payload.get("ref") or "").removeprefix("refs/heads/")
        noun = "commit" if count == 1 else "commits"
        return f"pushed {count} {noun} to {branch}" if branch else f"pushed {count} {noun}"

    if event_type == "PullRequestEvent":
        pr = payload.get("pull_request") or {}
        action = payload.get("action", "")
        verb = "merged" if action == "closed" and pr.get("merged") else action
        return f"{verb} PR #{payload.get('number')}: {pr.get('title', '')}"

    if event_type == "IssuesEvent":
        issue = payload.get("issue") or {}
        action = payload.get("action", "")
        return f"{action} issue #{issue.get('number')}: {issue.get('title', '')}"

    if event_type == "ReleaseEvent":
        release = payload.get("release") or {}
        return f"created release {release.get('tag_name', '')}"

    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type", "")
        ref = payload.get("ref") or ""
        return f"created {ref_type} {ref}".strip()

    return event_type


def _normalize_event(raw_event: dict) -> OrgEvent:
    actor = raw_event.get("actor") or {}
    repo = raw_event.get("repo") or {}
    return OrgEvent(
        id=str(raw_event["id"]),
        type=raw_event.get("type", ""),
        actor=actor.get("login", ""),
        actor_avatar=actor.get("avatar_url", ""),
        repo=repo.get("name", ""),
        summary=_summarize(raw_event),
        created_at=raw_event["created_at"],
    )


def _fetch_events(org_login: str, token: str, per_page: int) -> OrgEventsResponse:
    client = GitHubClient(token)
    raw_events = client.request("GET", f"/orgs/{org_login}/events", params={"per_page": per_page})
    if not isinstance(raw_events, list):
        # GitHub's events endpoint always returns a JSON array; a dict here means
        # GitHubClient's empty-body fallback (`{}`) kicked in on an unexpected 2xx
        # response -- surface that as an error rather than silently rendering it
        # as "no events".
        raise HTTPException(status_code=502, detail="Unexpected response from GitHub events API")
    events = [_normalize_event(e) for e in raw_events if not _is_bot(e)]
    return OrgEventsResponse(org=org_login, events=events)


def _cached_events(org_login: str, token: str, per_page: int) -> OrgEventsResponse:
    key = (org_login, _token_hash(token), per_page)
    now = time.monotonic()
    cached = _events_cache.get(key)
    if cached and now - cached[0] < _EVENTS_CACHE_TTL_SECONDS:
        return cached[1]
    events = _fetch_events(org_login, token, per_page)
    _events_cache[key] = (now, events)
    return events


@router.post("/github/orgs/{org_login}/events", response_model=OrgEventsResponse)
def org_events(
    org_login: str,
    payload: OrgEventsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=client_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return _cached_events(org_login, token, payload.per_page)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


# ---------------------------------------------------------------------------
# Full developer feed (docs/plan.md Phase 17) -- org-wide failed-run log and
# release timeline, both fanned out per-repo the same way security.py's
# compliance matrix is (best-effort per repo, capped repo count).
# ---------------------------------------------------------------------------


def _run_duration_seconds(run: dict) -> int | None:
    started = run.get("run_started_at")
    updated = run.get("updated_at")
    if not started or not updated or run.get("status") != "completed":
        return None
    try:
        start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = int((end_dt - start_dt).total_seconds())
    return delta if delta >= 0 else None


def _repo_failed_runs(client: GitHubClient, owner: str, repo: str) -> list[FailedRunSummary]:
    try:
        data = client.request("GET", f"/repos/{owner}/{repo}/actions/runs", params={"per_page": 30})
    except (httpx.HTTPStatusError, httpx.RequestError):
        return []
    raw_runs = data.get("workflow_runs", []) if isinstance(data, dict) else []

    # GitHub returns runs newest-first; group by workflow to walk each workflow's
    # own timeline independently (a failing workflow on one runs list mustn't count
    # a different workflow's success as breaking its streak).
    by_workflow: dict[int, list[dict]] = {}
    for run in raw_runs:
        by_workflow.setdefault(run.get("workflow_id"), []).append(run)

    summaries: list[FailedRunSummary] = []
    for runs in by_workflow.values():
        streak = 0
        for run in runs:
            if run.get("status") == "completed" and run.get("conclusion") == "failure":
                streak += 1
            else:
                break
        if streak < 3:
            continue
        latest = runs[0]
        summaries.append(
            FailedRunSummary(
                repo=f"{owner}/{repo}",
                workflow_name=latest.get("name") or "",
                branch=latest.get("head_branch", ""),
                run_id=latest["id"],
                started_at=latest.get("run_started_at") or latest["created_at"],
                duration_seconds=_run_duration_seconds(latest),
                url=latest.get("html_url", ""),
                actor=(latest.get("actor") or {}).get("login", ""),
                consecutive_failures=streak,
            )
        )
    return summaries


def _repo_releases(client: GitHubClient, owner: str, repo: str, cutoff: datetime) -> list[ReleaseSummary]:
    try:
        raw = client.request("GET", f"/repos/{owner}/{repo}/releases", params={"per_page": 20})
    except (httpx.HTTPStatusError, httpx.RequestError):
        return []
    if not isinstance(raw, list):
        return []

    releases = []
    for r in raw:
        published_at = r.get("published_at")
        if not published_at:
            continue
        try:
            published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if published < cutoff:
            continue
        body = (r.get("body") or "")[:120]
        releases.append(
            ReleaseSummary(
                repo=f"{owner}/{repo}",
                tag_name=r.get("tag_name", ""),
                name=r.get("name") or r.get("tag_name", ""),
                published_at=published_at,
                is_prerelease=bool(r.get("prerelease")),
                body_preview=body,
                url=r.get("html_url", ""),
            )
        )
    return releases


@router.post("/github/orgs/{org_login}/failed-runs", response_model=FailedRunsResponse)
def org_failed_runs(
    org_login: str,
    payload: FailedRunsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=client_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client = GitHubClient(token)
    try:
        repos = client.request_paginated(f"/orgs/{org_login}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    repo_names = [r["name"] for r in repos[:_MAX_REPOS_FOR_FEED]]

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = pool.map(lambda name: _repo_failed_runs(client, org_login, name), repo_names)
    runs = [r for repo_runs in results for r in repo_runs]
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return FailedRunsResponse(org=org_login, runs=runs[: payload.limit])


@router.post("/github/orgs/{org_login}/release-timeline", response_model=ReleaseTimelineResponse)
def org_release_timeline(
    org_login: str,
    payload: ReleaseTimelineInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=client_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client = GitHubClient(token)
    try:
        repos = client.request_paginated(f"/orgs/{org_login}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    repo_names = [r["name"] for r in repos[:_MAX_REPOS_FOR_FEED]]
    cutoff = datetime.now(timezone.utc) - timedelta(days=payload.days)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = pool.map(lambda name: _repo_releases(client, org_login, name, cutoff), repo_names)
    releases = [r for repo_releases in results for r in repo_releases]
    releases.sort(key=lambda r: r.published_at, reverse=True)
    return ReleaseTimelineResponse(org=org_login, releases=releases)
