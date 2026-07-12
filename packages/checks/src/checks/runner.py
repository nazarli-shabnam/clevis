import logging

from checks.github_checks import BranchProtectionEnabled, OrgMFARequired, SecretScanningEnabled, _get_all_pages

logger = logging.getLogger(__name__)


def run_all_checks(owner: str, token: str, base_url: str = "https://api.github.com") -> dict:
    repos = _get_all_pages(base_url, f"/orgs/{owner}/repos", token)

    checks = [OrgMFARequired(), BranchProtectionEnabled(), SecretScanningEnabled()]
    results = []
    for check in checks:
        try:
            output = check.run(owner=owner, token=token, base_url=base_url, repos=repos)
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
