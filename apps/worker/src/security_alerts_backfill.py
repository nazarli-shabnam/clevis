"""Backfill of already-open Dependabot and code scanning alerts into security_alerts.

The webhook path only sees alerts raised after the App was installed, so a repo with old critical alerts could
look clean until a new alert event arrived. This reads each repo's open Dependabot and code scanning
alerts through GitHub's REST list endpoints and records, per (repo, kind), that the list was read in
full (security_alert_syncs), which is what lets the API trust the stored rows for that repo and kind.

REST alert objects have the same fields as the webhook payloads' ``alert``, so they go through the event
consumer's own normalizer and the same upsert.
"""

from datetime import datetime, timedelta, timezone

import httpx

import event_consumer
from backfill import _get_with_retry, is_rate_limited_403

_PER_PAGE = 100
# Bound on the alert pages read per (repo, kind), so a looping Link header can't run forever. A listing
# that would exceed it is NOT recorded as complete.
_MAX_PAGES = 30
_MAX_REPOS = 500

# security_alerts.kind -> the list endpoint (all with state=open). Secret scanning is left out on purpose: its
# Security view lists every state from GitHub live, and open-only backfilled rows would make the stored
# path under-report resolved alerts.
_ALERT_PATHS = {
    "dependabot": "dependabot/alerts",
    "code_scanning": "code-scanning/alerts",
}
# security_alerts.kind -> the webhook event type the consumer's normalizer expects.
_EVENT_TYPE = {kind: f"{kind}_alert" for kind in _ALERT_PATHS}


class IncompleteListing(Exception):
    """A list could not be read in full (page cap or a non-list body); its alerts must not count as complete."""


def list_repos(client: httpx.Client, base: str, headers: dict, account_login: str, account_type: str) -> list[str]:
    """Full names of the repos the token can see, newest push first, capped at _MAX_REPOS."""
    if account_type == "User":
        path, params, items_key = "/installation/repositories", {"per_page": _PER_PAGE}, "repositories"
    else:
        path, params, items_key = f"/orgs/{account_login}/repos", {"per_page": _PER_PAGE, "sort": "pushed"}, None
    url = f"{base}{path}"
    repos: list[dict] = []
    for _ in range(_MAX_PAGES):
        resp = _get_with_retry(client, url, headers, params)
        resp.raise_for_status()
        body = resp.json()
        page = body.get(items_key, []) if items_key and isinstance(body, dict) else body
        if not isinstance(page, list):
            break
        repos.extend(r for r in page if isinstance(r, dict))
        next_link = resp.links.get("next")
        if not next_link or len(repos) >= _MAX_REPOS:
            break
        url, params = next_link["url"], None
    return [r["full_name"] for r in repos[:_MAX_REPOS] if isinstance(r.get("full_name"), str)]


def is_unreadable(error: httpx.HTTPStatusError) -> bool:
    """A genuine 4xx (e.g. 403: the token lacks the alerts permission): that (repo, kind) is unknown, so it is
    skipped and never recorded as complete. Rate limits and 5xx are not, and propagate so the job is retried."""
    resp = error.response
    return 400 <= resp.status_code < 500 and resp.status_code != 429 and not is_rate_limited_403(resp)


def fetch_open_alerts(client: httpx.Client, base: str, headers: dict, full_name: str, kind: str) -> list[dict]:
    """Every open alert of one kind for a repo. A 404 (the feature is off for the repo) is a real "no alerts".

    Raises httpx.HTTPStatusError for any other failure (see is_unreadable) and IncompleteListing past the page cap."""
    url = f"{base}/repos/{full_name}/{_ALERT_PATHS[kind]}"
    params: dict | None = {"state": "open", "per_page": _PER_PAGE}
    alerts: list[dict] = []
    for _ in range(_MAX_PAGES):
        resp = _get_with_retry(client, url, headers, params)
        if resp.status_code == 404:
            return []  # the feature is off for this repo: it has no alerts of this kind
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list):
            raise IncompleteListing(f"{full_name} {kind}: expected a list page")
        alerts.extend(a for a in page if isinstance(a, dict))
        next_link = resp.links.get("next")
        if not next_link:
            return alerts
        url, params = next_link["url"], None
    raise IncompleteListing(f"{full_name} {kind}: more than {_MAX_PAGES} pages")


def normalize(kind: str, full_name: str, alert: dict, received_at: datetime) -> dict | None:
    """security_alerts column values for one REST alert, or None if it lacks a number."""
    return event_consumer._normalize_security_alert(
        _EVENT_TYPE[kind], {"repository": {"full_name": full_name}, "alert": alert}, received_at
    )


# An alert created more recently than this is left for the alert sweep to announce: its webhook may simply not
# have been processed yet, and stamping it here would suppress the one notification it deserves.
_RECENT = timedelta(hours=1)


def is_old(created_at: datetime, received_at: datetime) -> bool:
    return created_at <= received_at - _RECENT


def mark_announced(cur, *, tenant_id: int, repo: str, kind: str, number: int) -> None:
    """Stamp a backfilled, pre-existing alert as already announced. Without this the alert sweep would message
    every destination about each old critical/high alert at once. Only for alerts old enough (is_old)."""
    cur.execute(
        "UPDATE security_alerts SET alert_notified_at = NOW() "
        "WHERE tenant_id = %s AND repo = %s AND kind = %s AND number = %s AND alert_notified_at IS NULL",
        (tenant_id, repo, kind, number),
    )


def close_missing(cur, *, tenant_id: int, repo: str, kind: str, open_numbers: list[int], fetched_before) -> None:
    """Close stored ``open`` alerts that a complete listing no longer contains (a close webhook was missed), so
    the stored rows the matrix trusts do not keep a stale open finding. Only rows Clevis stored before the
    listing began: one ingested since is newer than what was read and must not be closed from it."""
    cur.execute(
        "UPDATE security_alerts SET state = 'fixed', updated_at = NOW() "
        "WHERE tenant_id = %s AND repo = %s AND kind = %s AND state = 'open' "
        "AND NOT (number = ANY(%s)) AND ingested_at < %s",
        (tenant_id, repo, kind, open_numbers, fetched_before),
    )


def mark_synced(cur, *, tenant_id: int, repo: str, kind: str) -> None:
    """Record that (repo, kind)'s open alerts were read in full just now."""
    cur.execute(
        """
        INSERT INTO security_alert_syncs (tenant_id, repo, kind, synced_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (tenant_id, repo, kind) DO UPDATE SET synced_at = EXCLUDED.synced_at
        """,
        (tenant_id, repo, kind),
    )


def now() -> datetime:
    return datetime.now(timezone.utc)
