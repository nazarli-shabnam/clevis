import httpx

from checks.base import Check, CheckMetadata


def _get(url: str, token: str) -> dict | list:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        r = client.get(url, headers=headers)
    r.raise_for_status()
    return r.json()


def _get_all_pages(base_url: str, path: str, token: str) -> list:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    results = []
    url: str | None = f"{base_url}{path}?per_page=100"
    with httpx.Client(timeout=20) as client:
        while url:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            results.extend(r.json())
            url = None
            for part in r.headers.get("Link", "").split(","):
                part = part.strip()
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
    return results


def _branch_protection_status(exc: httpx.HTTPStatusError) -> str:
    code = exc.response.status_code
    if code == 404:
        return "unprotected"
    if code in (403, 429):
        return "unknown"
    return "unprotected"


class OrgMFARequired(Check):
    metadata = CheckMetadata(
        check_id="organization_members_mfa_required",
        title="Organization requires 2FA/MFA",
        severity="high",
        remediation="Require two-factor authentication for all org members in org settings.",
    )

    def run(
        self,
        owner: str,
        token: str,
        base_url: str = "https://api.github.com",
        repos: list | None = None,  # unused — MFA check operates at org level
    ) -> dict:
        org = _get(f"{base_url}/orgs/{owner}", token)
        if "two_factor_requirement_enabled" not in org:
            return {
                "status": "error",
                "value": "Token lacks org-owner scope to read MFA requirement status",
            }
        enabled = bool(org["two_factor_requirement_enabled"])
        return {"status": "pass" if enabled else "fail", "value": enabled}


class BranchProtectionEnabled(Check):
    metadata = CheckMetadata(
        check_id="repository_default_branch_protection_enabled",
        title="Default branch has protection rules",
        severity="high",
        remediation="Enable branch protection for default branch on all active repositories.",
    )

    def run(
        self,
        owner: str,
        token: str,
        base_url: str = "https://api.github.com",
        repos: list | None = None,
    ) -> dict:
        if repos is None:
            repos = _get_all_pages(base_url, f"/orgs/{owner}/repos", token)
        checked = 0
        protected = 0
        unknown = 0
        for repo in repos:
            checked += 1
            branch = repo.get("default_branch")
            try:
                details = _get(f"{base_url}/repos/{owner}/{repo['name']}/branches/{branch}", token)
                if details.get("protected"):
                    protected += 1
            except httpx.HTTPStatusError as exc:
                if _branch_protection_status(exc) == "unknown":
                    unknown += 1
        evaluable = checked - unknown
        if evaluable == 0:
            return {"status": "error", "value": {"checked": checked, "protected": protected, "unknown": unknown}}
        compliant = protected == evaluable
        return {
            "status": "pass" if compliant else "fail",
            "value": {"checked": checked, "protected": protected, "unknown": unknown},
        }


class SecretScanningEnabled(Check):
    metadata = CheckMetadata(
        check_id="repository_secret_scanning_enabled",
        title="Secret scanning enabled",
        severity="medium",
        remediation="Enable secret scanning for repositories where available.",
    )

    def run(
        self,
        owner: str,
        token: str,
        base_url: str = "https://api.github.com",
        repos: list | None = None,
    ) -> dict:
        if repos is None:
            repos = _get_all_pages(base_url, f"/orgs/{owner}/repos", token)
        enabled = 0
        total = len(repos)
        for repo in repos:
            sec = repo.get("security_and_analysis") or {}
            if sec.get("secret_scanning", {}).get("status") == "enabled":
                enabled += 1
        compliant = total > 0 and enabled == total
        return {"status": "pass" if compliant else "fail", "value": {"enabled": enabled, "total": total}}
