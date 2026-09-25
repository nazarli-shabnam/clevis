"""Stale-PR / stale-review nudges: post a one-line comment (or apply a ``needs-review``
label) on a PR that's sat without review activity past a configurable threshold.

Requires ``pull_requests: write``; a token without it gets a 400 pointing at
docs/self-hosting.md.

On-demand only: the API endpoint runs a single sweep when the user clicks the button. A
periodic background loop is deliberately out of scope here, tracked as a follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from src.services.github_client import GitHubClient

# app_config keys (registered in src.core.app_config._ACCEPTED_KEYS)
STALE_DAYS_KEY = "pr_nudge_stale_days"
MODE_KEY = "pr_nudge_mode"

DEFAULT_STALE_DAYS = 3
DEFAULT_MODE = "comment"
MODES = ("off", "comment", "label")

_NUDGE_MARKER = "<!-- clevis:pr-nudge -->"
_NUDGE_LABEL = "needs-review"
# Cap per sweep so one click can't post hundreds of comments on a big backlog.
_MAX_PER_SWEEP = 20


@dataclass
class NudgeResult:
    number: int
    title: str
    action: str  # "commented" | "labeled" | "skipped-recent-nudge" | "skipped-not-stale"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_stale(pr: dict, cutoff: datetime) -> bool:
    """Stale = opened before the cutoff, not a draft, and nobody has pushed or
    commented since the cutoff (``updated_at`` moves on any of those)."""
    if pr.get("draft"):
        return False
    updated = _parse_ts(pr.get("updated_at"))
    created = _parse_ts(pr.get("created_at"))
    if created is None or created > cutoff:
        return False
    return updated is None or updated <= cutoff


def _already_nudged(client: GitHubClient, owner: str, repo: str, number: int) -> bool:
    # Paginate: on a heavily-commented PR the marker would be past the first page
    # and we'd post a duplicate nudge.
    comments = client.request_paginated(f"/repos/{owner}/{repo}/issues/{number}/comments")
    return any(_NUDGE_MARKER in (c.get("body") or "") for c in comments)


def run_nudge_sweep(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    stale_days: int,
    mode: str,
    now: datetime | None = None,
) -> list[NudgeResult]:
    """List open PRs in ``{owner}/{repo}`` and nudge the stale ones.

    ``mode``:
      - ``"off"``     — return an empty list without calling GitHub's write API
      - ``"comment"`` — post a nudge comment (once; skips PRs already nudged)
      - ``"label"``   — add the ``needs-review`` label (idempotent on GitHub's side)
    """
    if mode == "off":
        return []
    if mode not in MODES:
        raise ValueError(f"unknown nudge mode: {mode!r}")

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=stale_days)

    # Paginate: a repo with >100 open PRs is exactly the stale-backlog case this
    # feature targets — a single page would silently ignore the rest.
    prs = client.request_paginated(
        f"/repos/{owner}/{repo}/pulls",
        params={"state": "open", "sort": "created", "direction": "asc"},
    )

    results: list[NudgeResult] = []
    acted = 0
    for pr in prs:
        number, title = pr["number"], pr.get("title", "")
        try:
            acted += _nudge_pr(client, owner, repo, pr, results, cutoff=cutoff, now=now, mode=mode,
                               cap_reached=acted >= _MAX_PER_SWEEP)
        except (httpx.HTTPStatusError, httpx.RequestError):
            # Nothing posted yet: let the caller map the error (e.g. a 403 permission hint).
            # Otherwise keep what was already posted so it's returned and audited, and stop.
            if acted == 0:
                raise
            results.append(NudgeResult(number, title, "error"))
            break

    return results


def _nudge_pr(
    client: GitHubClient, owner: str, repo: str, pr: dict, results: list[NudgeResult], *,
    cutoff: datetime, now: datetime, mode: str, cap_reached: bool,
) -> int:
    """Appends this PR's result; returns 1 if a comment/label was posted."""
    number, title = pr["number"], pr.get("title", "")
    if not _is_stale(pr, cutoff):
        results.append(NudgeResult(number, title, "skipped-not-stale"))
        return 0
    if cap_reached:
        results.append(NudgeResult(number, title, "skipped-per-sweep-cap"))
        return 0

    if mode == "label":
        client.request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/labels",
            json={"labels": [_NUDGE_LABEL]},
        )
        results.append(NudgeResult(number, title, "labeled"))
        return 1

    # mode == "comment"
    if _already_nudged(client, owner, repo, number):
        results.append(NudgeResult(number, title, "skipped-recent-nudge"))
        return 0
    age_days = (now - (_parse_ts(pr.get("created_at")) or now)).days
    client.request(
        "POST",
        f"/repos/{owner}/{repo}/issues/{number}/comments",
        json={
            "body": (
                f"This pull request has been open for {age_days} days without review "
                f"activity. Could a maintainer take a look?\n\n{_NUDGE_MARKER}"
            )
        },
    )
    results.append(NudgeResult(number, title, "commented"))
    return 1


