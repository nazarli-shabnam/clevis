"""Per-repo delivery-flow metrics from GitHub's REST API: PR cycle time, time to first review,
and per-workflow duration / failure rate / flakiness.

Bounded on purpose: a fixed window, a page cap on both list endpoints, and a cap on how many
merged PRs get a per-PR reviews lookup, so one call costs a predictable handful of GitHub requests.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from src.services.github_client import GitHubClient

WINDOW_DAYS = 30
_PAGE_SIZE = 100
_MAX_PAGES = 3
# Each merged PR in the sample costs one extra /reviews request.
_MAX_REVIEW_LOOKUPS = 30
_FAILED = {"failure", "timed_out"}
_COUNTED = _FAILED | {"success"}


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def _first_review_hours(client: GitHubClient, owner: str, repo: str, pr: dict) -> tuple[bool, float | None]:
    """(known, hours): hours from PR creation to the first review by someone other than the author.

    ``known`` is False when the lookup failed or the response was unusable, which is different from
    a PR that genuinely had no review (``(True, None)``)."""
    created = _ts(pr.get("created_at"))
    author = (pr.get("user") or {}).get("login")
    try:
        reviews = client.request("GET", f"/repos/{owner}/{repo}/pulls/{pr['number']}/reviews", params={"per_page": 100})
    except (httpx.HTTPStatusError, httpx.RequestError):
        return False, None
    if created is None or not isinstance(reviews, list):
        return False, None
    times = [
        t
        for r in reviews
        if (r.get("user") or {}).get("login") != author and (t := _ts(r.get("submitted_at"))) is not None
    ]
    return True, (max(_hours(min(times) - created), 0.0) if times else None)


def pr_metrics(client: GitHubClient, owner: str, repo: str, since: datetime) -> tuple[dict, bool]:
    """PR cycle/review stats, plus whether the page cap cut off merged PRs still inside the window."""
    merged: list[dict] = []
    truncated = False
    for page in range(1, _MAX_PAGES + 1):
        batch = client.request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc", "per_page": _PAGE_SIZE, "page": page},
        )
        if not isinstance(batch, list) or not batch:
            break
        merged.extend(p for p in batch if (m := _ts(p.get("merged_at"))) is not None and m >= since)
        oldest_updated = _ts(batch[-1].get("updated_at"))
        if len(batch) < _PAGE_SIZE or (oldest_updated is not None and oldest_updated < since):
            break
    else:
        truncated = True  # every page was full and still inside the window

    cycle = [
        _hours(m - c) for p in merged if (m := _ts(p.get("merged_at"))) is not None and (c := _ts(p.get("created_at"))) is not None
    ]
    sample = merged[:_MAX_REVIEW_LOOKUPS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        first_review = list(pool.map(lambda p: _first_review_hours(client, owner, repo, p), sample))
    reviewed = [h for known, h in first_review if h is not None]
    failed = sum(1 for known, _ in first_review if not known)
    return {
        "merged_count": len(merged),
        "median_cycle_hours": _median(cycle),
        "median_first_review_hours": _median(reviewed),
        "review_sample_size": len(sample),
        # Only PRs whose reviews were actually fetched and empty; failed lookups are counted apart.
        "merged_without_review": len(sample) - len(reviewed) - failed,
        "review_lookup_failed": failed,
    }, truncated


def workflow_metrics(client: GitHubClient, owner: str, repo: str, since: datetime) -> tuple[list[dict], bool]:
    """Per-workflow stats from completed runs in the window, plus whether the page cap truncated them."""
    runs: list[dict] = []
    truncated = False
    for page in range(1, _MAX_PAGES + 1):
        data = client.request(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs",
            params={"status": "completed", "created": f">={since.date().isoformat()}", "per_page": _PAGE_SIZE, "page": page},
        )
        batch = data.get("workflow_runs", []) if isinstance(data, dict) else []
        runs.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        truncated = page == _MAX_PAGES

    by_workflow: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_workflow[run.get("name") or str(run.get("workflow_id", "unknown"))].append(run)

    out = []
    for name, wf_runs in by_workflow.items():
        counted = [r for r in wf_runs if r.get("conclusion") in _COUNTED]
        failures = sum(1 for r in counted if r["conclusion"] in _FAILED)
        durations = [
            _hours(end - start) * 3600
            for r in wf_runs
            if (start := _ts(r.get("run_started_at"))) is not None and (end := _ts(r.get("updated_at"))) is not None
        ]
        # Flaky = the same commit saw both a failing and a passing run of this workflow. /actions/runs
        # lists only the latest attempt of a re-run, so a passing run with run_attempt > 1 stands in for the
        # failed attempt we can't see (a heuristic: a re-run is not always preceded by a failure).
        outcomes: dict[str, set[str]] = defaultdict(set)
        for r in counted:
            failed_run = r["conclusion"] in _FAILED
            outcomes[r.get("head_sha", "")].add("fail" if failed_run else "pass")
            if not failed_run and (r.get("run_attempt") or 1) > 1:
                outcomes[r.get("head_sha", "")].add("fail")
        out.append(
            {
                "name": name,
                "runs": len(wf_runs),
                "failure_rate": round(failures / len(counted), 3) if counted else None,
                "avg_duration_seconds": round(sum(durations) / len(durations)) if durations else None,
                "flaky_commits": sum(1 for o in outcomes.values() if o == {"fail", "pass"}),
            }
        )
    return out, truncated


def compute(client: GitHubClient, owner: str, repo: str, now: datetime | None = None) -> dict:
    since = (now or datetime.now(timezone.utc)) - timedelta(days=WINDOW_DAYS)
    workflows, truncated = workflow_metrics(client, owner, repo, since)
    prs, prs_truncated = pr_metrics(client, owner, repo, since)
    return {
        "repository": f"{owner}/{repo}",
        "window_days": WINDOW_DAYS,
        "prs": prs,
        "prs_truncated": prs_truncated,
        "workflows": workflows,
        "workflows_truncated": truncated,
    }
