"""Repository-hygiene checks: CODEOWNERS, SECURITY.md, license, stale branches, unpinned Actions.

All informational (`CheckMetadata.informational`): they don't affect the score unless the instance
opts in, and `analytics_service` only runs them at all when it will score them (see `run_all_checks`).

Scan cost is bounded: only the `_MAX_REPOS` most recently pushed active repos are inspected
(`sampled` in the result says when that cut repos off), each repo's file tree is fetched once and
shared by the file-presence and workflow checks, and the branch/workflow lookups are capped per repo.
"""

from __future__ import annotations

import base64
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx

from checks.base import Check, CheckMetadata
from checks import github_checks as _gh

_MAX_REPOS = 15
_MAX_BRANCH_LOOKUPS = 5
_MAX_WORKFLOW_FILES = 5
_WORKERS = 8
STALE_DAYS = 90
# Actions from these owners are treated as first-party (still worth pinning, but not "third-party").
_FIRST_PARTY_OWNERS = {"actions", "github"}

_USES_RE = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)", re.MULTILINE)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Key under which one scan's per-repo tree lookup is memoized on the repo dict the runner shares
# between checks, so CODEOWNERS/SECURITY.md/workflows cost one tree request per repo, not three.
_TREE_KEY = "_hygiene_paths"
# Set on the repo dict by a check that had to look at only part of a repo (e.g. only some branches), so
# `run` can report `sampled` for it the same way it does when only some repos were inspected.
_PARTIAL_KEY = "_hygiene_partial"
_BRANCHES_PAGE_SIZE = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _repo_paths(base_url: str, owner: str, repo: dict, token: str) -> tuple[set[str], bool] | None:
    """(file paths on the default branch, whether GitHub truncated the tree), or None for an empty repo."""
    if _TREE_KEY in repo:
        return repo[_TREE_KEY]
    try:
        tree = _gh._get(
            f"{base_url}/repos/{owner}/{repo['name']}/git/trees/{repo.get('default_branch')}?recursive=1", token
        )
    except httpx.HTTPStatusError as exc:
        # 409: empty repository. 404 only means "empty" when there is no default branch to look up;
        # otherwise it's a failed request (missing tree permission, vanished branch) -> unknown.
        if exc.response.status_code == 409 or (exc.response.status_code == 404 and not repo.get("default_branch")):
            repo[_TREE_KEY] = None
            return None
        raise
    result = ({e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"}, bool(tree.get("truncated")))
    repo[_TREE_KEY] = result
    return result


class _PerRepoCheck(Check):
    """Runs `_inspect` over a bounded sample of repos and aggregates pass/fail like the other checks.

    `_inspect` returns (ok, extra): ok is True/False for evaluable repos, None when the repo can't be
    judged (API error); `extra` is a count to total up (stale branches, unpinned uses), or 0.
    """

    def _inspect(self, base_url: str, owner: str, repo: dict, token: str) -> tuple[bool | None, int]:
        raise NotImplementedError

    def run(
        self,
        owner: str,
        token: str,
        base_url: str = "https://api.github.com",
        repos: list | None = None,
        account_type: str = "Organization",
    ) -> dict:
        if repos is None:
            repos = _gh._get_all_pages(base_url, f"/orgs/{owner}/repos", token)
        active = [r for r in repos if not r.get("archived")]
        if not active:
            return {"status": "not_applicable", "value": {"checked": 0, "missing": 0, "unknown": 0, "sampled": 0}}
        active.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
        sample = active[:_MAX_REPOS]

        def safe(repo: dict) -> tuple[bool | None, int]:
            try:
                return self._inspect(base_url, owner, repo, token)
            except httpx.HTTPError:
                return None, 0

        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            outcomes = list(pool.map(safe, sample))

        # `None` = unknown (API error); `skip` repos (empty) report ok=True with no extra, so they pass.
        unknown = sum(1 for ok, _ in outcomes if ok is None)
        evaluable = [o for o in outcomes if o[0] is not None]
        if not evaluable:
            return {"status": "error", "value": {"checked": 0, "missing": 0, "unknown": unknown, "sampled": 0}}
        missing = sum(1 for ok, _ in evaluable if not ok)
        value = {
            "checked": len(evaluable),
            "missing": missing,
            "unknown": unknown,
            "sampled": 1 if len(active) > _MAX_REPOS or any(r.get(_PARTIAL_KEY) for r in sample) else 0,
        }
        extra_total = sum(extra for _, extra in evaluable)
        if extra_total:
            value[self.extra_label] = extra_total
        return {"status": "pass" if missing == 0 else "fail", "value": value}

    extra_label = "count"


class _FilePresentCheck(_PerRepoCheck):
    paths: tuple[str, ...] = ()

    def _inspect(self, base_url, owner, repo, token):
        found = _repo_paths(base_url, owner, repo, token)
        if found is None:
            return True, 0  # empty repository: nothing to require
        present, truncated = found
        if any(p in present for p in self.paths):
            return True, 0
        # A truncated tree may simply not list the file, so don't call that a miss.
        return (None if truncated else False), 0


class CodeownersPresent(_FilePresentCheck):
    metadata = CheckMetadata(
        check_id="repository_codeowners_present",
        title="CODEOWNERS file present",
        severity="low",
        remediation="Add a CODEOWNERS file (.github/CODEOWNERS) so reviews are routed to owners.",
        informational=True,
    )
    paths = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


class SecurityPolicyPresent(_FilePresentCheck):
    metadata = CheckMetadata(
        check_id="repository_security_policy_present",
        title="SECURITY.md present",
        severity="low",
        remediation="Add a SECURITY.md describing how to report vulnerabilities.",
        informational=True,
    )
    paths = ("SECURITY.md", ".github/SECURITY.md", "docs/SECURITY.md")


class LicensePresent(_FilePresentCheck):
    metadata = CheckMetadata(
        check_id="repository_license_present",
        title="License present",
        severity="low",
        remediation="Add a LICENSE file so others know how they may use the code.",
        informational=True,
    )

    paths = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "UNLICENSE")

    def _inspect(self, base_url, owner, repo, token):
        # Free path: the repo listing carries GitHub's detected license. It is null for a custom or
        # unrecognized LICENSE file too, so only then fall back to looking for the file itself.
        if repo.get("license") is not None:
            return True, 0
        return super()._inspect(base_url, owner, repo, token)


class StaleBranches(_PerRepoCheck):
    metadata = CheckMetadata(
        check_id="repository_no_stale_branches",
        title=f"No branches idle for {STALE_DAYS}+ days",
        severity="low",
        remediation="Delete or merge branches that have had no commits in 90 days.",
        informational=True,
    )
    extra_label = "stale_branches"

    def _inspect(self, base_url, owner, repo, token):
        branches = _gh._get(
            f"{base_url}/repos/{owner}/{repo['name']}/branches?per_page={_BRANCHES_PAGE_SIZE}", token
        )
        # Protected branches (release lines, etc.) are deliberately long-lived.
        candidates = [b for b in branches if b.get("name") != repo.get("default_branch") and not b.get("protected")]
        # Each branch costs a commit lookup, so only some are checked; say so when others were left out
        # (a full first page means there may be more branches than we even listed).
        if len(candidates) > _MAX_BRANCH_LOOKUPS or len(branches) >= _BRANCHES_PAGE_SIZE:
            repo[_PARTIAL_KEY] = True
        cutoff = _now() - timedelta(days=STALE_DAYS)
        stale = 0
        for b in candidates[:_MAX_BRANCH_LOOKUPS]:
            commit = _gh._get(f"{base_url}/repos/{owner}/{repo['name']}/commits/{b['commit']['sha']}", token)
            when = ((commit.get("commit") or {}).get("committer") or {}).get("date")
            try:
                if when and datetime.fromisoformat(when.replace("Z", "+00:00")) < cutoff:
                    stale += 1
            except ValueError:
                continue  # one unparsable date must not make the whole check unknown
        return stale == 0, stale


def unpinned_third_party_uses(workflow_text: str, owner: str) -> list[str]:
    """`uses:` references to third-party actions that aren't pinned to a full commit SHA."""
    bad = []
    for ref in _USES_RE.findall(workflow_text):
        if ref.startswith(("./", "docker://")) or "@" not in ref:
            continue  # local action, container image, or malformed
        name, _, version = ref.partition("@")
        action_owner = name.split("/", 1)[0].lower()
        if action_owner in _FIRST_PARTY_OWNERS or action_owner == owner.lower():
            continue
        if not _SHA_RE.match(version):
            bad.append(ref)
    return bad


class UnpinnedActions(_PerRepoCheck):
    metadata = CheckMetadata(
        check_id="repository_actions_pinned_to_sha",
        title="Third-party Actions pinned to a commit SHA",
        severity="low",
        remediation="Pin third-party actions to a full commit SHA instead of a tag or branch.",
        informational=True,
    )
    extra_label = "unpinned_uses"

    def _inspect(self, base_url, owner, repo, token):
        found = _repo_paths(base_url, owner, repo, token)
        if found is None:
            return True, 0
        files = sorted(
            p for p in found[0] if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))
        )[:_MAX_WORKFLOW_FILES]
        unpinned = 0
        for path in files:
            body = _gh._get(f"{base_url}/repos/{owner}/{repo['name']}/contents/{path}", token)
            text = base64.b64decode(body.get("content", "")).decode("utf-8", errors="replace")
            unpinned += len(unpinned_third_party_uses(text, owner))
        if unpinned == 0 and found[1]:
            return None, 0  # a truncated tree may omit workflow files, so "none found" proves nothing
        return unpinned == 0, unpinned


HYGIENE_CHECKS = (CodeownersPresent, SecurityPolicyPresent, LicensePresent, StaleBranches, UnpinnedActions)
