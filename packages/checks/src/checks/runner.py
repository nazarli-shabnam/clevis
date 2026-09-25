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
    ]

    # The prefetch feeds every check, so degrade to per-check "error" results here too.
    try:
        repos = _fetch_repos(base_url, owner, token, account_type)
    except Exception:
        logger.exception("failed to fetch repo list for %s", owner)
        results = [
            {
                "id": check.metadata.check_id,
                "title": check.metadata.title,
                "severity": check.metadata.severity,
                "remediation": check.metadata.remediation,
                "status": "error",
                "value": "Check failed: could not fetch repository list",
            }
            for check in checks
        ]
        return {"checks": results, "repo_count": 0}
    # Archived repos are read-only (nothing "Fix this" can change) and empty repos have no
    # default branch to protect; neither should count against the score.
    # ponytail: size == 0 is GitHub's empty-repo signal; a brand-new tiny repo can briefly
    # report 0 too, and is re-counted on the next scan.
    repos = [r for r in repos if not r.get("archived") and r.get("size", 1) != 0]

    results = []
    for check in checks:
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
            "status": output["status"],
            "value": output["value"],
        })
    return {"checks": results, "repo_count": len(repos)}
