"""Org activity feed — proxies GitHub's `/orgs/{org}/events`, normalized into
human-readable summaries. POST (not GET) so an optional client-supplied PAT travels in
the body, never a URL/query string.
"""

import asyncio
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.core.db import RepoEvent, RepoEventDailyCount, SessionLocal, get_db
from src.core.rbac import OrgContext, require_org_role, set_tenant_session_context
from src.repositories import installation_repo
from src.schemas.github import (
    ActivitySummaryEntry,
    ActivitySummaryResponse,
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

logger = logging.getLogger(__name__)

# repo_events.event_type is the lowercase vocabulary the worker writes; the UI's filter
# chips match GitHub's raw PascalCase strings (e.g. "PushEvent") -- this map keeps the
# response contract identical regardless of which path served it.
_EVENT_TYPE_TO_GITHUB = {
    "push": "PushEvent",
    "pull_request": "PullRequestEvent",
    "issues": "IssuesEvent",
    "release": "ReleaseEvent",
    "create": "CreateEvent",
}

router = APIRouter()

# Each repo costs one additional GitHub call for failed-runs/release-timeline, on top
# of the initial repo list -- bounds per-repo fan-out.
_MAX_REPOS_FOR_FEED = 20

# Short TTL, well under the frontend's 30s poll interval -- collapses concurrent polls
# from multiple viewers of the same org into one upstream call.
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
        # GitHubClient's empty-body fallback kicked in on an unexpected 2xx response.
        raise HTTPException(status_code=502, detail="Unexpected response from GitHub events API")
    events = [_normalize_event(e) for e in raw_events if not _is_bot(e)]
    return OrgEventsResponse(org=org_login, events=events)


def _fetch_events_from_repo_events(db: Session, org_login: str, tenant_id: int, per_page: int) -> OrgEventsResponse:
    """Serves the Activity Feed from the already-populated repo_events table instead of a
    live GitHub call -- no token, no rate-limit exposure, no cache needed. Bot-filtered
    here at read time; repo_events itself stores every actor unfiltered by design."""
    rows = (
        db.query(RepoEvent)
        .filter(RepoEvent.tenant_id == tenant_id, ~RepoEvent.actor.like("%[bot]"))
        .order_by(RepoEvent.occurred_at.desc())
        .limit(per_page)
        .all()
    )
    events = [
        OrgEvent(
            id=str(row.id),
            type=_EVENT_TYPE_TO_GITHUB.get(row.event_type, row.event_type),
            actor=row.actor,
            actor_avatar=row.actor_avatar,
            repo=row.repo,
            summary=row.summary,
            created_at=row.occurred_at,
        )
        for row in rows
    ]
    return OrgEventsResponse(org=org_login, events=events)


def _evict_expired_events(now: float) -> None:
    # Same reasoning as repos.py's _evict_expired_stats: token_hash rotates hourly with
    # each fresh installation token, so stale keys would otherwise accumulate forever.
    expired = [key for key, (cached_at, _) in _events_cache.items() if now - cached_at >= _EVENTS_CACHE_TTL_SECONDS]
    for key in expired:
        del _events_cache[key]


def _cached_events(org_login: str, token: str, per_page: int) -> OrgEventsResponse:
    key = (org_login, _token_hash(token), per_page)
    now = time.monotonic()
    cached = _events_cache.get(key)
    if cached and now - cached[0] < _EVENTS_CACHE_TTL_SECONDS:
        return cached[1]
    events = _fetch_events(org_login, token, per_page)
    _events_cache[key] = (now, events)
    _evict_expired_events(now)
    return events


@router.post("/github/orgs/{org_login}/events", response_model=OrgEventsResponse)
def org_events(
    org_login: str,
    payload: OrgEventsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    # repo_events only has rows for a tenant with a connected GitHub App installation; a
    # legacy PAT-only org falls through to the live-GitHub path below to avoid an empty feed.
    # get_for_org can return a row with installation_id IS NULL (re-synced org metadata
    # without a real installation), so installation_id is checked explicitly here too.
    installation = installation_repo.get_for_org(db, org_id=ctx.org.id, account_login=org_login)
    if installation is not None and installation.installation_id is not None:
        from_db = _fetch_events_from_repo_events(db, org_login, ctx.org.tenant_id, payload.per_page)
        if from_db.events:
            return from_db
        # repo_events being empty usually means the webhooks aren't subscribed yet or the
        # install backfill aged out -- fall through to a live-GitHub read rather than show
        # a permanently blank feed.

    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=client_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return _cached_events(org_login, token, payload.per_page)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


# Upper bound on a client-supplied window (not a default) -- keeps a malicious
# `days=100000` from summing an unbounded number of daily-count rows.
_ACTIVITY_SUMMARY_MAX_DAYS = 90
_ACTIVITY_SUMMARY_DEFAULT_DAYS = 7

# SSE poll cadence and a hard cap on stream duration -- poll-and-diff against the
# aggregate table rather than LISTEN/NOTIFY or pub/sub; a client past the cap just
# reconnects, same as any SSE gateway timeout would force anyway.
_SSE_POLL_INTERVAL_SECONDS = 5
_SSE_MAX_DURATION_SECONDS = 15 * 60
# Upper bound on a single snapshot read. The stream offloads it to a threadpool worker
# with abandon_on_cancel=False, so a blocked read would also block cancellation --
# statement_timeout lets Postgres kill a stuck read so the worker can return.
_SSE_SNAPSHOT_TIMEOUT_MS = 20_000


def _org_installation_connected(db: Session, org_login: str, ctx: OrgContext) -> bool:
    # Same installation_id-presence guard as org_events -- repo_event_daily_counts is
    # only ever non-empty for a tenant with a real connected installation.
    installation = installation_repo.get_for_org(db, org_id=ctx.org.id, account_login=org_login)
    return installation is not None and installation.installation_id is not None


def _activity_summary_snapshot(db: Session, org_login: str, ctx: OrgContext, days: int) -> ActivitySummaryResponse:
    connected = _org_installation_connected(db, org_login, ctx)
    totals: list[ActivitySummaryEntry] = []
    if connected:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=days - 1)
        rows = (
            db.query(RepoEventDailyCount.repo, RepoEventDailyCount.event_type, func.sum(RepoEventDailyCount.count))
            .filter(RepoEventDailyCount.tenant_id == ctx.org.tenant_id, RepoEventDailyCount.day >= cutoff)
            .group_by(RepoEventDailyCount.repo, RepoEventDailyCount.event_type)
            .all()
        )
        totals = [ActivitySummaryEntry(repo=repo, event_type=event_type, count=int(count)) for repo, event_type, count in rows]
    return ActivitySummaryResponse(
        org=org_login, days=days, connected=connected, generated_at=datetime.now(timezone.utc), totals=totals
    )


def _teardown_stream_session(db: Session) -> None:
    # app.tenant_id/app.user_id are set with plain SET (not SET LOCAL), so they persist on
    # the pooled connection unless RESET before checkin; on failure, invalidate() rather
    # than risk handing a still-tenant-scoped connection to an unrelated request.
    try:
        db.rollback()
        db.execute(text("RESET app.tenant_id"))
        db.execute(text("RESET app.user_id"))
        db.commit()
        db.close()
    except Exception:
        logger.exception("failed to reset SSE stream session context; invalidating connection instead of reusing it")
        db.invalidate()


@contextmanager
def _stream_poll_session():
    """A fresh short-lived Session for one poll, torn down before the caller sleeps until
    the next poll. The stream must NOT hold one Session open for its whole <=15-min
    lifetime, or a handful of idle streams would exhaust the connection pool."""
    db = SessionLocal()
    try:
        yield db
    finally:
        _teardown_stream_session(db)


def _run_activity_summary_poll(session_scope, org_login: str, ctx: OrgContext, days: int) -> ActivitySummaryResponse:
    """One snapshot read on its own session (see _stream_poll_session). Runs in a
    threadpool worker via anyio.to_thread; the SET LOCAL statement_timeout bounds a read
    that blocks so stream cancellation isn't stuck waiting on Postgres."""
    with session_scope() as db:
        set_tenant_session_context(db, ctx.org.tenant_id, ctx.membership.user_id)
        db.execute(text(f"SET LOCAL statement_timeout = {int(_SSE_SNAPSHOT_TIMEOUT_MS)}"))
        return _activity_summary_snapshot(db, org_login, ctx, days)


async def _activity_summary_stream(
    org_login: str,
    ctx: OrgContext,
    days: int,
    poll_interval: float = _SSE_POLL_INTERVAL_SECONDS,
    *,
    session_scope=_stream_poll_session,
):
    """Async SSE body generator. Uses `await asyncio.sleep` (not blocking `time.sleep`) so
    a stream open for up to _SSE_MAX_DURATION_SECONDS doesn't pin one of Starlette's shared
    threadpool tokens; each poll's DB snapshot runs on its own short-lived session,
    offloaded to a thread (`session_scope` is injectable for tests).

    Only emits a real `activity_summary` event when the aggregate changed since the last
    poll; otherwise emits an SSE heartbeat comment so intermediary proxies don't close an
    idle-looking connection."""
    deadline = time.monotonic() + _SSE_MAX_DURATION_SECONDS
    last_key: tuple | None = None
    while time.monotonic() < deadline:
        snapshot = await anyio.to_thread.run_sync(_run_activity_summary_poll, session_scope, org_login, ctx, days)
        key = (snapshot.connected, tuple(sorted((t.repo, t.event_type, t.count) for t in snapshot.totals)))
        if key != last_key:
            yield f"event: activity_summary\ndata: {snapshot.model_dump_json()}\n\n"
            last_key = key
        else:
            yield ": heartbeat\n\n"
        await asyncio.sleep(poll_interval)


@router.get("/github/orgs/{org_login}/activity-summary", response_model=ActivitySummaryResponse)
def org_activity_summary(
    org_login: str,
    days: int = _ACTIVITY_SUMMARY_DEFAULT_DAYS,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    """Per-repo, per-event-type event counts over a trailing window, read from the
    pre-computed repo_event_daily_counts rollup -- no live GitHub call, no token.

    Returns connected=False with empty totals (200, not an error) for a legacy PAT-only
    org with no GitHub App installation."""
    days = max(1, min(days, _ACTIVITY_SUMMARY_MAX_DAYS))
    return _activity_summary_snapshot(db, org_login, ctx, days)


@router.get("/github/orgs/{org_login}/activity-summary/stream")
async def org_activity_summary_stream(
    org_login: str,
    days: int = _ACTIVITY_SUMMARY_DEFAULT_DAYS,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
):
    """SSE channel pushing the same shape org_activity_summary returns, whenever it
    changes. See _activity_summary_stream.

    No `Depends(get_db)`: FastAPI tears a yield-dependency down as soon as the handler
    *returns* the StreamingResponse, before the body has streamed -- so the stream opens
    its own per-poll sessions instead (see _stream_poll_session)."""
    days = max(1, min(days, _ACTIVITY_SUMMARY_MAX_DAYS))
    return StreamingResponse(_activity_summary_stream(org_login, ctx, days), media_type="text/event-stream")


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

    # GitHub returns runs newest-first; group by workflow so a different workflow's
    # success doesn't break this workflow's failure streak.
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
        # A malformed run entry (missing id/created_at) shouldn't 500 the whole repo.
        if "id" not in latest or ("run_started_at" not in latest and "created_at" not in latest):
            continue
        summaries.append(
            FailedRunSummary(
                repo=f"{owner}/{repo}",
                workflow_name=latest.get("name") or "",
                branch=latest.get("head_branch", ""),
                run_id=latest["id"],
                started_at=latest.get("run_started_at") or latest.get("created_at"),
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
