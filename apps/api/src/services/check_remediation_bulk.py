"""Apply one security-check fix ("Fix this") across many repos in an org.

Reuses the single-repo fixes in ``check_remediation`` and adds what a batch needs on top:

* a read-only **preview** that says, per repo, whether the fix would change anything and what it
  would do, using the same decision the apply step makes;
* an **apply** that skips repos that are already fine (a repo whose default branch is already
  protected is not rewritten just because the batch touched it) and captures per-repo failures,
  so one inaccessible or conflicting repo doesn't abort the rest.

Needs the same GitHub write permissions as the single-repo fix (``administration:write``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import httpx

from src.services import check_remediation
from src.services.branch_protection_bulk import _REPO_NAME_RE
from src.services.check_remediation import RemediationConflict, _get_branch_protection, _preserving_put_body
from src.services.github_client import GitHubClient

Status = Literal["would_change", "unchanged", "applied", "failed"]

_SECRET_SCANNING = "repository_secret_scanning_enabled"
_BRANCH_PROTECTION = "repository_default_branch_protection_enabled"
_NO_FORCE_PUSH = "repository_default_branch_no_force_push"


@dataclass
class BulkItem:
    repo: str
    status: Status
    detail: str = ""


def _force_pushes_allowed(current: dict) -> bool:
    value = current.get("allow_force_pushes")
    return bool(value.get("enabled")) if isinstance(value, dict) else bool(value)


def needs_change(client: GitHubClient, check_id: str, owner: str, repo: str) -> tuple[bool, str]:
    """Whether applying ``check_id`` to ``owner/repo`` would change anything, and a one-line
    description of it. Read-only. Raises ``RemediationConflict`` when the fix could not be applied
    safely, and lets httpx errors propagate."""
    info = client.request("GET", f"/repos/{owner}/{repo}")
    info = info if isinstance(info, dict) else {}

    if check_id == _SECRET_SCANNING:
        analysis = info.get("security_and_analysis")
        status = ((analysis or {}).get("secret_scanning") or {}).get("status") if isinstance(analysis, dict) else None
        if status == "enabled":
            return False, "Secret scanning is already enabled."
        if status is None:
            return True, "Will enable secret scanning (its current state isn't visible to this token)."
        return True, "Will enable secret scanning."

    if check_id in (_BRANCH_PROTECTION, _NO_FORCE_PUSH):
        branch = info.get("default_branch") or "main"
        path = f"/repos/{owner}/{repo}/branches/{quote(branch, safe='')}/protection"
        current = _get_branch_protection(client, path)
        if current is None:
            return (
                True,
                f"'{branch}' has no protection: will require 1 approving review and block "
                "force-pushes and branch deletion.",
            )
        if check_id == _BRANCH_PROTECTION:
            return False, f"'{branch}' is already protected."
        if _force_pushes_allowed(current):
            _preserving_put_body(current)  # raises RemediationConflict if it can't be kept safely
            return True, f"Will turn off force-pushes on '{branch}', keeping every other rule."
        return False, f"Force-pushes are already blocked on '{branch}'."

    raise check_remediation.RemediationNotSupported(check_id)


def _failure(exc: Exception) -> str:
    if isinstance(exc, RemediationConflict):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return f"GitHub API error: {exc.response.status_code}"
    return "GitHub API unreachable"


def plan_bulk(client: GitHubClient, check_id: str, owner: str, repos: list[str]) -> list[BulkItem]:
    """Preview: per repo, would the fix change anything? Writes nothing."""
    items: list[BulkItem] = []
    for repo in repos:
        if not _REPO_NAME_RE.match(repo):
            items.append(BulkItem(repo, "failed", "invalid repository name"))
            continue
        try:
            needed, detail = needs_change(client, check_id, owner, repo)
        except (RemediationConflict, httpx.HTTPStatusError, httpx.RequestError) as exc:
            items.append(BulkItem(repo, "failed", _failure(exc)))
            continue
        items.append(BulkItem(repo, "would_change" if needed else "unchanged", detail))
    return items


def apply_bulk(client: GitHubClient, check_id: str, owner: str, repos: list[str]) -> list[BulkItem]:
    """Apply the fix to each repo that needs it; repos that are already fine are left untouched."""
    items: list[BulkItem] = []
    for repo in repos:
        if not _REPO_NAME_RE.match(repo):
            items.append(BulkItem(repo, "failed", "invalid repository name"))
            continue
        try:
            needed, detail = needs_change(client, check_id, owner, repo)
            if not needed:
                items.append(BulkItem(repo, "unchanged", detail))
                continue
            check_remediation.remediate(client, check_id, owner, repo)
        except (RemediationConflict, httpx.HTTPStatusError, httpx.RequestError) as exc:
            items.append(BulkItem(repo, "failed", _failure(exc)))
            continue
        items.append(BulkItem(repo, "applied", detail))
    return items
