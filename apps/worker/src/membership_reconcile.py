"""Full org-roster GitHub fetch for the membership-reconciliation job.

Mirrors apps/api/src/routers/collab.py's endpoints and fallbacks; retry helpers are
duplicated from backfill.py since apps/worker doesn't import apps/api.
"""

import time

import httpx

_PER_PAGE = 100
_MAX_RETRY_AFTER_SECONDS = 60


class RosterIncomplete(Exception):
    """Raised when _get_all_pages can't return a trustworthy complete page set.

    reconcile_org_members DELETEs anyone missing from the member list, so a partial list
    would wipe real members. Never swallow this for members/admins/outside_collaborators;
    the best-effort 2FA overlay treats it as a failed overlay.
    """


def _get_all_pages(client: httpx.Client, base: str, headers: dict, path: str, params: dict) -> list[dict]:
    """Follow Link: rel="next" with no page cap (large orgs are legitimate).

    Raises RosterIncomplete on a pagination loop or a non-list/non-JSON body, and
    httpx.HTTPStatusError/RequestError once retries are exhausted."""
    results: list[dict] = []
    url = f"{base}{path}"
    page_params: dict | None = {**params, "per_page": _PER_PAGE}
    seen_urls: set[str] = set()
    while True:
        if url in seen_urls:
            raise RosterIncomplete(f"{path!r} pagination looped back to an already-fetched page")
        seen_urls.add(url)
        resp = _get_with_retry(client, url, headers, page_params)
        resp.raise_for_status()
        try:
            page = resp.json()
        except ValueError as error:
            raise RosterIncomplete(f"non-JSON page body from {path!r}: {error}") from error
        if not isinstance(page, list):
            raise RosterIncomplete(f"expected a list page from {path!r}, got {type(page).__name__}")
        if any(not isinstance(item, dict) or not isinstance(item.get("login"), str) or not item["login"] for item in page):
            # Fail closed: a dropped login would look like a departure and DELETE that member's row.
            raise RosterIncomplete(f"invalid roster entry from {path!r}")
        results.extend(page)
        next_link = resp.links.get("next")
        if not next_link:
            break
        url = next_link["url"]
        page_params = None  # already encoded in the next link's URL
    return results


def _is_secondary_rate_limit(resp: httpx.Response) -> bool:
    if resp.status_code != 403:
        return False
    return "Retry-After" in resp.headers or resp.headers.get("X-RateLimit-Remaining") == "0"


def _retry_delay_seconds(resp: httpx.Response, attempt: int) -> float:
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


def fetch_org_roster(client: httpx.Client, base: str, headers: dict, org_login: str) -> dict:
    """Return {"members": [...], "two_factor_disabled_logins": set|None, "outside_logins": set}.

    A member is "admin" iff in the role=admin-filtered call (the plain list has no role).
    two_factor_disabled_logins is None if the overlay failed (needs org-owner scope), kept
    distinct from an empty set so callers don't overwrite known-good data.
    """
    admins_raw = _get_all_pages(client, base, headers, f"/orgs/{org_login}/members", {"role": "admin"})
    all_raw = _get_all_pages(client, base, headers, f"/orgs/{org_login}/members", {"role": "all"})
    admin_logins = {m["login"] for m in admins_raw if "login" in m}
    members = [
        {
            "login": m["login"],
            "avatar_url": m.get("avatar_url", ""),
            "role": "admin" if m["login"] in admin_logins else "member",
        }
        for m in all_raw
        if "login" in m
    ]

    two_factor_disabled_logins: set[str] | None
    try:
        no_2fa_raw = _get_all_pages(client, base, headers, f"/orgs/{org_login}/members", {"filter": "2fa_disabled"})
        two_factor_disabled_logins = {m["login"] for m in no_2fa_raw if "login" in m}
    except (httpx.HTTPStatusError, httpx.RequestError, RosterIncomplete):
        two_factor_disabled_logins = None

    outside_raw = _get_all_pages(client, base, headers, f"/orgs/{org_login}/outside_collaborators", {})
    outside_logins = {c["login"] for c in outside_raw if "login" in c}

    return {
        "members": members,
        "two_factor_disabled_logins": two_factor_disabled_logins,
        "outside_logins": outside_logins,
    }
