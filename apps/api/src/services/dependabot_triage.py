"""Auto-triage low-risk Dependabot PRs.

High-risk (approving/merging is hard to undo), so every safety rail is on by default:
per-repo opt-in (default disabled), ``mode`` defaults to ``approve_only``, and a PR is
only acted on when all of: author is dependabot[bot], not draft, patch-level bump only,
all checks green, and no pending/requested human review. Capped per-run (default 5);
every decision is audit-logged by the router.

Requires ``pull_requests: write`` (approve) + ``contents: write`` (merge); a 403 becomes
a 400 pointing at docs/self-hosting.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from src.services.github_client import GitHubClient

DEPENDABOT_LOGIN = "dependabot[bot]"
MODE_APPROVE_ONLY = "approve_only"
MODE_APPROVE_AND_MERGE = "approve_and_merge"
MODES = (MODE_APPROVE_ONLY, MODE_APPROVE_AND_MERGE)
DEFAULT_CAP = 5

# Dependabot PR bodies open with e.g. "Bumps [lodash](...) from 4.17.20 to 4.17.21.".
# A grouped PR has several such lines ("Updates `x` from ... to ...", "Updates `y` ...").
# The trailing (?![.\w-]) rejects a 4-part or pre-release target ("1.2.3.4", "1.2.3-rc1")
# so those fall through to "undeterminable" -> skip.
_BUMP_RE = re.compile(
    r"\bfrom\s+v?(\d+\.\d+\.\d+)\s+to\s+v?(\d+\.\d+\.\d+)(?![-\w]|\.\d)",
    re.IGNORECASE,
)

# Any "Bumps/Updates ... from X to Y" line, to catch update lines `_BUMP_RE` can't
# classify (pre-release, 4-part, bare Action bumps) -- those must fail closed, not drop.
_UPDATE_LINE_RE = re.compile(r"\b(?:bumps|updates)\b.+?\bfrom\s+\S+\s+to\s+\S+", re.IGNORECASE)


@dataclass
class Decision:
    number: int | None
    title: str
    action: str  # approved | merged | merge_failed | would_approve | would_merge | skipped | error
    reason: str = ""


def _bump_is_patch(body: str) -> bool | None:
    """``True`` only when every "from X.Y.Z to X.Y.Z" line in the body is a same-major,
    same-minor, non-decreasing patch bump. ``None`` when the body has no recognisable
    bump line at all (caller skips — fail closed). A grouped PR that bundles a minor/
    major bump alongside a patch one returns ``False``, not ``True``."""
    seen_update_line = False
    seen_patch = False
    seen_non_patch = False  # a line we could parse and know is not a patch bump
    seen_unparseable = False  # an update line whose targets we can't classify at all
    for line in (body or "").splitlines():
        if not _UPDATE_LINE_RE.search(line):
            continue
        seen_update_line = True
        matches = _BUMP_RE.findall(line)
        if not matches:
            seen_unparseable = True
            continue
        for old_s, new_s in matches:
            old = tuple(int(x) for x in old_s.split("."))
            new = tuple(int(x) for x in new_s.split("."))
            if old[0] == new[0] and old[1] == new[1] and new[2] >= old[2]:
                seen_patch = True
            else:
                seen_non_patch = True

    if not seen_update_line:
        return None
    if seen_non_patch:
        return False
    # An unparseable line bundled alongside a real patch bump must reject the PR, not ride
    # along on the patch line. On its own it's just "undeterminable" -> caller skips.
    if seen_unparseable:
        return False if seen_patch else None
    return True if seen_patch else None


def _checks_all_green(client: GitHubClient, owner: str, repo: str, sha: str) -> bool:
    """Every check-run and every classic commit status on ``sha`` is a completed success.
    Requires at least one signal -- a head SHA with no CI at all is not-green. If GitHub
    reports more check-runs than the one page fetched, they can't all be verified, so
    not green."""
    runs = client.request(
        "GET", f"/repos/{owner}/{repo}/commits/{sha}/check-runs", params={"per_page": 100}
    )
    check_runs = runs.get("check_runs", []) if isinstance(runs, dict) else []
    total = runs.get("total_count", len(check_runs)) if isinstance(runs, dict) else 0
    if total > len(check_runs):
        return False  # a failing run could be hiding on a page we didn't fetch
    for run in check_runs:
        if run.get("status") != "completed":
            return False
        if run.get("conclusion") not in ("success", "neutral", "skipped"):
            return False

    status = client.request("GET", f"/repos/{owner}/{repo}/commits/{sha}/status")
    state = status.get("state") if isinstance(status, dict) else None
    statuses = status.get("statuses", []) if isinstance(status, dict) else []
    if statuses and state != "success":
        return False

    return bool(check_runs) or state == "success"


_APPROVAL_BODY = "Auto-approved by Clevis: patch-level Dependabot bump, all checks green."


def _review_state(client: GitHubClient, owner: str, repo: str, pr: dict) -> tuple[bool, bool]:
    """(a human review blocks, Clevis already approved this PR). Clevis's own approval is
    recognised by its fixed review body, independent of which token/bot identity posted it."""
    if pr.get("requested_reviewers") or pr.get("requested_teams"):
        return True, False
    reviews = client.request_paginated(f"/repos/{owner}/{repo}/pulls/{pr['number']}/reviews")
    blocked = any(r.get("state") == "CHANGES_REQUESTED" for r in reviews)
    approved = any(r.get("state") == "APPROVED" and r.get("body") == _APPROVAL_BODY for r in reviews)
    return blocked, approved


def _evaluate(client: GitHubClient, owner: str, repo: str, pr: dict) -> tuple[str | None, bool]:
    """(skip-reason or ``None`` when eligible, whether Clevis already approved it)."""
    if (pr.get("user") or {}).get("login") != DEPENDABOT_LOGIN:
        return "not a Dependabot PR", False
    if pr.get("draft"):
        return "draft PR", False
    patch = _bump_is_patch(pr.get("body", ""))
    if patch is None:
        return "could not determine the bump level from the PR body", False
    if not patch:
        return "not a patch-level bump", False
    sha = (pr.get("head") or {}).get("sha")
    if not sha or not _checks_all_green(client, owner, repo, sha):
        return "checks are not all green", False
    blocked, approved = _review_state(client, owner, repo, pr)
    if blocked:
        return "a human review is pending or requested changes", approved
    return None, approved


def triage(
    client: GitHubClient,
    owner: str,
    repo: str,
    *,
    enabled: bool,
    mode: str,
    merge_method: str = "squash",
    cap: int = DEFAULT_CAP,
    dry_run: bool = False,
) -> list[Decision]:
    """One repo. Returns a decision per open PR (acted on, or skipped-with-reason).
    ``enabled=False`` short-circuits to an empty list."""
    if not enabled:
        return []

    prs = client.request_paginated(
        f"/repos/{owner}/{repo}/pulls", params={"state": "open"}
    )
    decisions: list[Decision] = []
    acted = 0
    for pr in prs:
        number, title = pr.get("number"), pr.get("title", "")
        try:
            acted += _triage_pr(client, owner, repo, pr, decisions, mode=mode, merge_method=merge_method,
                                cap_reached=acted >= cap, dry_run=dry_run)
        except httpx.HTTPStatusError:
            # Nothing acted on yet in this repo: let the caller classify the error (e.g. a 403
            # permission hint). Otherwise keep the approvals/merges already made so they are
            # returned and audited, and stop this repo.
            if not any(d.action in _WRITE_ACTIONS for d in decisions):
                raise
            decisions.append(Decision(number, title, "error", "GitHub API error; stopped this repo"))
            break
        except httpx.RequestError:
            if not any(d.action in _WRITE_ACTIONS for d in decisions):
                raise
            decisions.append(Decision(number, title, "error", "GitHub API unreachable; stopped this repo"))
            break

    return decisions


_WRITE_ACTIONS = ("approved", "merged", "merge_failed")


def _triage_pr(
    client: GitHubClient, owner: str, repo: str, pr: dict, decisions: list[Decision], *,
    mode: str, merge_method: str, cap_reached: bool, dry_run: bool,
) -> int:
    """Appends this PR's decision(s); returns 1 if it counts against the per-run cap."""
    number, title = pr.get("number"), pr.get("title", "")
    reason, already_approved = _evaluate(client, owner, repo, pr)
    will_merge = mode == MODE_APPROVE_AND_MERGE
    if reason is None and already_approved and not will_merge:
        reason = "already approved by Clevis"
    if reason is not None:
        decisions.append(Decision(number, title, "skipped", reason))
        return 0
    if cap_reached:
        decisions.append(Decision(number, title, "skipped", "per-run cap reached"))
        return 0

    if dry_run:
        decisions.append(Decision(number, title, "would_merge" if will_merge else "would_approve"))
        return 1

    approved_now = False
    if not already_approved:
        client.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            json={"event": "APPROVE", "body": _APPROVAL_BODY},
        )
        approved_now = True
    if will_merge:
        try:
            # Pin the merge to the head commit whose checks were verified green: if a new
            # commit landed since, GitHub rejects the merge (409) instead of merging unchecked code.
            client.request(
                "PUT",
                f"/repos/{owner}/{repo}/pulls/{number}/merge",
                json={"merge_method": merge_method, "sha": (pr.get("head") or {}).get("sha")},
            )
            decisions.append(Decision(number, title, "merged"))
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            # The approval already landed on GitHub -- record it as its own decision
            # and report the merge failure separately, rather than discarding it.
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f"the merge request failed: {exc.response.status_code}"
            else:
                detail = "the merge request could not be sent, so the merge outcome is unknown"
            if approved_now:
                decisions.append(Decision(number, title, "approved"))
            decisions.append(
                Decision(number, title, "merge_failed", f"approved, but {detail}")
            )
    else:
        decisions.append(Decision(number, title, "approved"))
    return 1
