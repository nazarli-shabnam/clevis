"""Install-time backfill of recent activity into repo_events via the GitHub Events API.

GitHub caps the Events API at ~90 days / ~300 events. The org/user feed is public-only, so private repos
are read separately and capped (see fetch_private_repo_events); history of private repos beyond that cap
arrives only through webhooks. Synthetic delivery_id
("backfill:<event id>") keeps retries idempotent via ON CONFLICT (tenant_id, delivery_id)
(per tenant: GitHub event ids are global, and one event can sit in several tenants' feeds).
The webhook path uses GitHub's delivery GUID, so the same event can arrive both ways; the worker
skips a backfilled event that has a webhook twin (repo_events_store.consume_webhook_twin).
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


# GitHub's org/user events feed is public-only, so private repositories are read one by one. Bounded so a
# backfill (at install, and on every gap-heal) costs at most 1 + this many extra requests per tenant;
# the most recently pushed private repos are taken first and each contributes one page of events.
_MAX_PRIVATE_REPOS = 50


def _is_skippable(error: httpx.HTTPStatusError) -> bool:
    """A genuine 4xx (e.g. the token can't read that repo): not worth failing the whole backfill for.
    Rate limits and 5xx are not skippable; they propagate so the job is requeued."""
    resp = error.response
    return 400 <= resp.status_code < 500 and resp.status_code != 429 and not _is_secondary_rate_limit(resp)


def _list_private_repos(client: httpx.Client, base: str, headers: dict, account_login: str, account_type: str) -> list[str]:
    """Full names of the most recently pushed private repos the token can read (installation tokens only
    see repos the App was granted, which is what we want)."""
    if account_type == "User":
        # A personal install has no /users/{login}/repos view of private repos for an installation token.
        path, params = "/installation/repositories", {"per_page": 100}
    else:
        path, params = f"/orgs/{account_login}/repos", {"type": "private", "sort": "pushed", "direction": "desc", "per_page": _MAX_PRIVATE_REPOS}
    resp = _get_with_retry(client, f"{base}{path}", headers, params)
    resp.raise_for_status()
    body = resp.json()
    repos = body.get("repositories", []) if isinstance(body, dict) else body
    if not isinstance(repos, list):
        return []
    private = [r for r in repos if isinstance(r, dict) and r.get("private") is True and isinstance(r.get("full_name"), str)]
    private.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    return [r["full_name"] for r in private[:_MAX_PRIVATE_REPOS]]


def fetch_private_repo_events(client: httpx.Client, base: str, headers: dict, account_login: str, account_type: str) -> list[dict]:
    """Recent events of the account's private repos, which the org/user events feed never includes.

    Best effort per call: a genuine 4xx (missing permission, repo gone) skips that call. Rate limits, 5xx
    and network errors propagate like fetch_events's, so the job is retried."""
    try:
        repos = _list_private_repos(client, base, headers, account_login, account_type)
    except httpx.HTTPStatusError as error:
        if _is_skippable(error):
            return []
        raise
    events: list[dict] = []
    for full_name in repos:
        try:
            resp = _get_with_retry(client, f"{base}/repos/{full_name}/events", headers, {"per_page": _PER_PAGE})
            resp.raise_for_status()
        except httpx.HTTPStatusError as error:
            if _is_skippable(error):
                continue
            raise
        page = resp.json()
        if isinstance(page, list):
            events.extend(page)
    return events


def fetch_all_events(client: httpx.Client, base: str, headers: dict, account_login: str, account_type: str) -> list[dict]:
    """The public events feed plus private-repo events, one entry per GitHub event id."""
    merged: dict[str, dict] = {}
    for event in (
        *fetch_events(client, base, headers, account_login, account_type),
        *fetch_private_repo_events(client, base, headers, account_login, account_type),
    ):
        merged.setdefault(str(event.get("id")), event)
    return list(merged.values())


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
