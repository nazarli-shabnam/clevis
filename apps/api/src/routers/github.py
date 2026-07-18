"""GET-equivalent org activity feed — proxies GitHub's `/orgs/{org}/events` and
normalizes each event into a human-readable summary (docs/plan.md Phase 9).

Implemented as POST (not the literal GET the plan doc sketches) so an optional
client-supplied PAT travels in the request body, never a URL/query string —
matching every other GitHub-token-bearing endpoint in this codebase
(src.routers.repos's *Input models).
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.schemas.github import OrgEvent, OrgEventsInput, OrgEventsResponse
from src.services.github_client import GitHubClient
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()


def _github_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        return HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    if isinstance(exc, httpx.RequestError):
        return HTTPException(status_code=503, detail="GitHub API unreachable")
    raise exc


def _is_bot(raw_event: dict) -> bool:
    login = (raw_event.get("actor") or {}).get("login", "")
    return login.endswith("[bot]")


def _summarize(raw_event: dict) -> str:
    event_type = raw_event.get("type", "")
    payload = raw_event.get("payload") or {}

    if event_type == "PushEvent":
        commits = payload.get("commits") or []
        branch = (payload.get("ref") or "").removeprefix("refs/heads/")
        count = len(commits)
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


@router.post("/orgs/{org_login}/events", response_model=OrgEventsResponse)
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
        client = GitHubClient(token)
        raw_events = client.request("GET", f"/orgs/{org_login}/events", params={"per_page": payload.per_page})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc
    events = [_normalize_event(e) for e in raw_events if not _is_bot(e)]
    return OrgEventsResponse(org=org_login, events=events)
