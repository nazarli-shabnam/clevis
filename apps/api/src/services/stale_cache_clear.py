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


def clear_idle_caches(client: GitHubClient, owner: str, repo: str, idle_days: int, now: datetime | None = None) -> int:
    """Delete caches whose ``last_accessed_at`` is older than ``idle_days``; returns how many were deleted.

    The list is requested oldest-access first, so reading stops at the first cache that is still in use. A cache
    that is already gone (404) is skipped. Any other GitHub error propagates to the caller."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=idle_days)
    base = f"/repos/{owner}/{repo}/actions/caches"
    deleted = 0
    while deleted < MAX_DELETES_PER_REPO:
        # Always page 1: what was deleted drops out of the list, so the next first page is the next oldest.
        data = client.request("GET", base, params={"sort": "last_accessed_at", "direction": "asc", "per_page": _PAGE})
        entries = data.get("actions_caches", []) if isinstance(data, dict) else []
        stale = [e for e in entries if _idle(e, cutoff)]
        if not stale:
            break
        progressed = 0
        for entry in stale:
            if deleted >= MAX_DELETES_PER_REPO:
                break
            try:
                client.request("DELETE", f"{base}/{entry['id']}")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                continue
            deleted += 1
            progressed += 1
        if len(stale) < len(entries) or progressed == 0:
            break  # reached a cache still in use, or nothing could be deleted
    return deleted


def _idle(entry: dict, cutoff: datetime) -> bool:
    raw = entry.get("last_accessed_at")
    if not isinstance(raw, str) or entry.get("id") is None:
        return False  # unknown last use: never delete on a guess
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) < cutoff
    except ValueError:
        return False
