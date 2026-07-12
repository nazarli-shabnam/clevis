import logging

import httpx

from checks.github_checks import BranchProtectionEnabled, OrgMFARequired, SecretScanningEnabled, _get_all_pages

logger = logging.getLogger(__name__)


def _resolve_account(base_url: str, owner: str, token: str) -> tuple[str, str]:
    """Return (repos_path, account_type) for owner login."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        user_resp = client.get(f"{base_url}/users/{owner}", headers=headers)
        if user_resp.status_code == 200:
            data = user_resp.json()
            if data.get("type") == "User":
                return f"/users/{owner}/repos", "User"
        org_resp = client.get(f"{base_url}/orgs/{owner}", headers=headers)
        if org_resp.status_code == 200:
            return f"/orgs/{owner}/repos", "Organization"
    raise RuntimeError(f"Could not resolve GitHub account for owner {owner!r}")


def run_all_checks(owner: str, token: str, base_url: str = "https://api.github.com") -> dict:
    repos_path, account_type = _resolve_account(base_url, owner, token)
    repos = _get_all_pages(base_url, repos_path, token)

    checks = [OrgMFARequired(), BranchProtectionEnabled(), SecretScanningEnabled()]
    results = []
    for check in checks:
        if account_type == "User" and check.metadata.check_id == "organization_members_mfa_required":
            output = {
                "status": "not_applicable",
                "value": "Organization MFA requirement does not apply to personal accounts",
            }
        else:
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
