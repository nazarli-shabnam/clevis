"""Delete a repository's GitHub Actions caches that nobody has used for N days (the unattended
``stale_cache_clear`` automation in services/automation_sweep.py)."""

from datetime import datetime, timedelta, timezone

import httpx

from src.services.github_client import GitHubClient

DEFAULT_IDLE_DAYS = 7
MAX_IDLE_DAYS = 90
# Bounds one run's GitHub calls per repo; the rest is cleared by the next run.
MAX_DELETES_PER_REPO = 500
_PAGE = 100
# Bounds the read when a repo has long runs of caches with an unknown last use.
_MAX_PAGES = 20


def clear_idle_caches(client: GitHubClient, owner: str, repo: str, idle_days: int, now: datetime | None = None) -> int:
    """Delete caches whose ``last_accessed_at`` is older than ``idle_days``; returns how many were deleted.

    The list is requested oldest-access first, so reading stops at the first cache that is still in use. Caches
    with an unknown last use are skipped (never deleted) but do not end the read, so an eligible cache behind a
    page of them is still found. The ids are collected before any delete, so deleting cannot shift the pages being
    read. A cache that is already gone (404) is skipped. Any other GitHub error propagates to the caller."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=idle_days)
    base = f"/repos/{owner}/{repo}/actions/caches"
    stale: list = []
    for page in range(1, _MAX_PAGES + 1):
        params = {"sort": "last_accessed_at", "direction": "asc", "per_page": _PAGE, "page": page}
        data = client.request("GET", base, params=params)
        entries = data.get("actions_caches", []) if isinstance(data, dict) else []
        in_use = False
        for entry in entries:
            idle = _idle(entry, cutoff)
            if idle:
                stale.append(entry["id"])
            elif _last_use_known(entry):
                in_use = True
                break
        if in_use or len(entries) < _PAGE or len(stale) >= MAX_DELETES_PER_REPO:
            break
    deleted = 0
    for cache_id in stale[:MAX_DELETES_PER_REPO]:
        try:
            client.request("DELETE", f"{base}/{cache_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            continue
        deleted += 1
    return deleted


def _last_use_known(entry: dict) -> bool:
    raw = entry.get("last_accessed_at")
    if not isinstance(raw, str) or entry.get("id") is None:
        return False
    try:
        datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _idle(entry: dict, cutoff: datetime) -> bool:
    raw = entry.get("last_accessed_at")
    if not isinstance(raw, str) or entry.get("id") is None:
        return False  # unknown last use: never delete on a guess
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) < cutoff
    except ValueError:
        return False
