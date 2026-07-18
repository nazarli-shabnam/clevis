import time

import httpx

from checks.base import Check, CheckMetadata


def _get_with_retry(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    # Same retry/backoff contract as GitHubClient.request (apps/api/src/services/github_client.py):
    # 3 attempts, exponential backoff on connection errors or a 429.
    for attempt in range(3):
        try:
            r = client.get(url, headers=headers)
        except httpx.RequestError:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise
        if r.status_code == 429 and attempt < 2:
            time.sleep(2**attempt)
            continue
        return r
    raise RuntimeError("request loop exhausted without returning")


def _get(url: str, token: str) -> dict | list:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        r = _get_with_retry(client, url, headers)
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
            r = _get_with_retry(client, url, headers)
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
        if len(repos) == 0:
            return {"status": "not_applicable", "value": {"checked": 0, "protected": 0, "unknown": 0}}
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
            except httpx.HTTPError:
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
        total = len(repos)
        if total == 0:
            return {"status": "not_applicable", "value": {"enabled": 0, "total": 0}}
        enabled = 0
        for repo in repos:
            sec = repo.get("security_and_analysis") or {}
            if sec.get("secret_scanning", {}).get("status") == "enabled":
                enabled += 1
        compliant = enabled == total
        return {"status": "pass" if compliant else "fail", "value": {"enabled": enabled, "total": total}}


class DependabotAlertsCheck(Check):
    metadata = CheckMetadata(
        check_id="repository_dependabot_alerts_clear",
        title="No open critical/high Dependabot alerts",
        severity="high",
        remediation="Resolve or dismiss open Dependabot alerts, prioritizing critical and high severity.",
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
        if len(repos) == 0:
            return {"status": "not_applicable", "value": {"critical": 0, "high": 0, "medium": 0, "low": 0}}
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for repo in repos:
            try:
                alerts = _get(
                    f"{base_url}/repos/{owner}/{repo['name']}/dependabot/alerts?state=open", token
                )
            except httpx.HTTPStatusError as exc:
                # Dependabot alerts disabled/inaccessible for this repo (404) or the
                # token lacks the security-events scope for it (403) — treat as no
                # alerts for that repo rather than failing the whole check.
                if exc.response.status_code in (403, 404):
                    continue
                raise
            for alert in alerts:
                severity = (alert.get("security_advisory") or {}).get("severity")
                if severity in counts:
                    counts[severity] += 1
        compliant = counts["critical"] == 0 and counts["high"] == 0
        return {"status": "pass" if compliant else "fail", "value": counts}


class CodeScanningCheck(Check):
    metadata = CheckMetadata(
        check_id="repository_code_scanning_alerts_clear",
        title="No open code scanning alerts",
        severity="medium",
        remediation="Resolve open code scanning alerts surfaced by CodeQL or a connected SAST tool.",
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
        total_repos = len(repos)
        if total_repos == 0:
            return {"status": "not_applicable", "value": {"open": 0, "repos_with_alerts": 0, "total_repos": 0}}
        open_count = 0
        repos_with_alerts = 0
        for repo in repos:
            try:
                alerts = _get(
                    f"{base_url}/repos/{owner}/{repo['name']}/code-scanning/alerts?state=open", token
                )
            except httpx.HTTPStatusError as exc:
                # Code scanning not enabled for this repo (404) or no access (403) —
                # treat as no alerts for that repo rather than failing the whole check.
                if exc.response.status_code in (403, 404):
                    continue
                raise
            if alerts:
                repos_with_alerts += 1
                open_count += len(alerts)
        value = {"open": open_count, "repos_with_alerts": repos_with_alerts, "total_repos": total_repos}
        return {"status": "pass" if open_count == 0 else "fail", "value": value}


class DefaultBranchNoForcePushCheck(Check):
    metadata = CheckMetadata(
        check_id="repository_default_branch_no_force_push",
        title="Default branch disallows force pushes",
        severity="high",
        remediation="Disable 'Allow force pushes' in the default branch's protection rules.",
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
        if len(repos) == 0:
            return {"status": "not_applicable", "value": {"repos_checked": 0, "force_push_allowed": 0}}
        checked = 0
        force_push_allowed = 0
        unknown = 0
        for repo in repos:
            branch = repo.get("default_branch")
            try:
                details = _get(f"{base_url}/repos/{owner}/{repo['name']}/branches/{branch}", token)
            except httpx.HTTPError:
                # Unprotected (404), rate-limited/no-access (403/429), or a network
                # error — force-push status can't be evaluated, so exclude the repo
                # from the denominator rather than counting it as compliant.
                unknown += 1
                continue
            checked += 1
            protection = details.get("protection") or {}
            allow_force_pushes = (protection.get("allow_force_pushes") or {}).get("enabled")
            if allow_force_pushes:
                force_push_allowed += 1
        if checked == 0:
            return {"status": "error", "value": {"repos_checked": checked, "force_push_allowed": force_push_allowed}}
        return {
            "status": "pass" if force_push_allowed == 0 else "fail",
            "value": {"repos_checked": checked, "force_push_allowed": force_push_allowed},
        }
