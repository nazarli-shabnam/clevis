"""Install-time backfill of recent activity into repo_events via the GitHub Events API.

GitHub caps the Events API at ~90 days / ~300 events. Synthetic delivery_id
("backfill:<event id>") keeps retries idempotent via ON CONFLICT (delivery_id).
"""

import time
from datetime import datetime

import httpx

# Bound on top of GitHub's own ~300-event cap, so a looping Link header can't run forever.
_MAX_PAGES = 3
_PER_PAGE = 100

# Cap on a server-supplied Retry-After so a malformed/huge value can't stall a job.
_MAX_RETRY_AFTER_SECONDS = 60


def _is_secondary_rate_limit(resp: httpx.Response) -> bool:
    """GitHub's secondary rate limit often returns 403 (not 429) with Retry-After or
    X-RateLimit-Remaining: 0; a genuine permission-denied 403 has neither."""
    if resp.status_code != 403:
        return False
    return "Retry-After" in resp.headers or resp.headers.get("X-RateLimit-Remaining") == "0"


def _retry_delay_seconds(resp: httpx.Response, attempt: int) -> float:
    """Prefer Retry-After, then X-RateLimit-Reset, both capped at _MAX_RETRY_AFTER_SECONDS.

    With neither header, a rate-limit 429/403 waits the full cap; other statuses (5xx)
    fall back to exponential backoff."""
    raw = resp.headers.get("Retry-After")
    if raw is not None:
        try:
            return min(float(raw), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    reset_raw = resp.headers.get("X-RateLimit-Reset")
    if reset_raw is not None:
        try:
            delay = float(reset_raw) - time.time()
            if delay > 0:
                return min(delay, _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    if resp.status_code == 429 or _is_secondary_rate_limit(resp):
        return _MAX_RETRY_AFTER_SECONDS
    return 2**attempt


def _get_with_retry(client: httpx.Client, url: str, headers: dict, params: dict | None) -> httpx.Response:
    """GET with 3 attempts: backoff on connection errors, rate limits, and 5xx."""
    for attempt in range(3):
        try:
            resp = client.get(url, headers=headers, params=params)
        except httpx.RequestError:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise
        if (resp.status_code == 429 or _is_secondary_rate_limit(resp) or resp.status_code >= 500) and attempt < 2:
            time.sleep(_retry_delay_seconds(resp, attempt))
            continue
        return resp
    raise RuntimeError("request loop exhausted without returning")

_EVENT_TYPE_MAP = {
    "PushEvent": "push",
    "PullRequestEvent": "pull_request",
    "IssuesEvent": "issues",
    "ReleaseEvent": "release",
    "CreateEvent": "create",
}


def _events_path(account_login: str, account_type: str) -> str:
    # account_type mirrors github_installations.account_type ("User" for a personal install).
    if account_type == "User":
        return f"/users/{account_login}/events"
    return f"/orgs/{account_login}/events"


def fetch_events(client: httpx.Client, base: str, headers: dict, account_login: str, account_type: str) -> list[dict]:
    """Follow Link: rel="next" up to _MAX_PAGES.

    Raises httpx.HTTPStatusError/RequestError once retries are exhausted."""
    events: list[dict] = []
    url = f"{base}{_events_path(account_login, account_type)}"
    params: dict | None = {"per_page": _PER_PAGE}
    for _ in range(_MAX_PAGES):
        resp = _get_with_retry(client, url, headers, params)
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list):
            break
        events.extend(page)
        next_link = resp.links.get("next")
        if not next_link:
            break
        url = next_link["url"]
        params = None  # already encoded in the next link's URL
    return events


def _summarize(event_type: str, payload: dict) -> str:
    """Per-event-type summary text, adapted to the Events API's nested `payload` shape."""
    if event_type == "push":
        size = payload.get("size")
        commits = payload.get("commits") or []
        count = size if isinstance(size, int) else len(commits)
        branch = (payload.get("ref") or "").removeprefix("refs/heads/")
        noun = "commit" if count == 1 else "commits"
        return f"pushed {count} {noun} to {branch}" if branch else f"pushed {count} {noun}"

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action", "")
        verb = "merged" if action == "closed" and pr.get("merged") else action
        return f"{verb} PR #{payload.get('number')}: {pr.get('title', '')}"

    if event_type == "issues":
        issue = payload.get("issue") or {}
        action = payload.get("action", "")
        return f"{action} issue #{issue.get('number')}: {issue.get('title', '')}"

    if event_type == "release":
        release = payload.get("release") or {}
        return f"created release {release.get('tag_name', '')}"

    if event_type == "create":
        ref_type = payload.get("ref_type", "")
        ref = payload.get("ref") or ""
        return f"created {ref_type} {ref}".strip()

    return event_type


def normalize(raw_event: dict) -> dict | None:
    """Return repo_events column values (minus tenant_id) for one event, or None if
    untracked or malformed."""
    event_type = _EVENT_TYPE_MAP.get(raw_event.get("type"))
    if event_type is None:
        return None
    event_id = raw_event.get("id")
    repo = (raw_event.get("repo") or {}).get("name")
    created_at_raw = raw_event.get("created_at")
    if not event_id or not repo or not created_at_raw:
        return None
    try:
        occurred_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    actor = raw_event.get("actor") or {}
    payload = raw_event.get("payload") or {}
    return {
        "delivery_id": f"backfill:{event_id}",
        "event_type": event_type,
        "actor": actor.get("login", ""),
        "actor_avatar": actor.get("avatar_url", ""),
        "repo": repo,
        "summary": _summarize(event_type, payload),
        "occurred_at": occurred_at,
    }
