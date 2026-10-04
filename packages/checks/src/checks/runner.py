import logging

import httpx

from checks.github_checks import (
    BranchProtectionEnabled,
    CodeScanningCheck,
    DefaultBranchNoForcePushCheck,
    DependabotAlertsCheck,
    OrgMFARequired,
    SecretScanningEnabled,
    _get_all_pages,
)
from checks.hygiene_checks import HYGIENE_CHECKS

logger = logging.getLogger(__name__)


def _fetch_repos(base_url: str, owner: str, token: str, account_type: str) -> list:
    """Repo list for either a GitHub org or a personal (User-type) account.

    A User token may be an App installation token or a PAT, so try
    /installation/repositories first and fall back to /user/repos on 401/403."""
    if account_type != "User":
        return _get_all_pages(base_url, f"/orgs/{owner}/repos", token)
    try:
        return _get_all_pages(base_url, "/installation/repositories", token, items_key="repositories")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            return _get_all_pages(base_url, "/user/repos?affiliation=owner&type=all", token)
        raise


def run_all_checks(
    owner: str, token: str, base_url: str = "https://api.github.com", account_type: str = "Organization"
) -> dict:
    checks = [
        OrgMFARequired(),
        BranchProtectionEnabled(),
        SecretScanningEnabled(),
        DependabotAlertsCheck(),
        CodeScanningCheck(),
        DefaultBranchNoForcePushCheck(),
        *(cls() for cls in HYGIENE_CHECKS),
    ]

    # The prefetch feeds the per-repo checks. If it fails they degrade to per-check "error" results,
    # but a check that doesn't read the repo list (org MFA) still runs on its own: a transient
    # failure here must not also fail a check that never needed it.
    repos: list = []
    prefetch_failed = False
    try:
        fetched = _fetch_repos(base_url, owner, token, account_type)
    except Exception:
        logger.exception("failed to fetch repo list for %s", owner)
        prefetch_failed = True
    else:
        # Archived repos are read-only: nothing "Fix this" can change, so they don't count against
        # the score. (Empty repos are handled per check: their default branch doesn't exist.)
        repos = [r for r in fetched if not r.get("archived")]

    results = []
    for check in checks:
        if prefetch_failed and check.requires_repos:
            output = {"status": "error", "value": "Check failed: could not fetch repository list"}
        else:
            try:
                output = check.run(owner=owner, token=token, base_url=base_url, repos=repos, account_type=account_type)
            except Exception:
                logger.exception("check %s failed", check.metadata.check_id)
                output = {
                    "status": "error",
                    "value": f"Check failed: {check.metadata.check_id}",
                }
        results.append({
            "id": check.metadata.check_id,
            "title": check.metadata.title,
            "severity": check.metadata.severity,
            "remediation": check.metadata.remediation,
            "informational": check.metadata.informational,
            "status": output["status"],
            "value": output["value"],
        })
    return {"checks": results, "repo_count": len(repos)}
