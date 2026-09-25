import logging
import time

import httpx

from checks.base import Check, CheckMetadata

logger = logging.getLogger(__name__)

# Page cap (5,000 items at per_page=100) so a huge org can't OOM the api process;
# truncating with a warning degrades completeness instead of crashing the scan.
_MAX_PAGES = 50


def _is_secondary_rate_limit(r: httpx.Response) -> bool:
    """GitHub's secondary rate limit returns 403 (not 429), usually with Retry-After."""
    if r.status_code != 403:
        return False
    return "Retry-After" in r.headers or r.headers.get("X-RateLimit-Remaining") == "0"

# Cap on a server-supplied Retry-After so a huge value can't stall a scan.
_MAX_RETRY_AFTER_SECONDS = 60


def _retry_delay_seconds(r: httpx.Response, attempt: int) -> float:
    """Delay before retrying: Retry-After, then X-RateLimit-Reset, then a fallback.

    Rate-limit responses with neither header wait the full cap (GitHub's documented
    minimum backoff); other statuses (5xx) use exponential backoff."""
    raw = r.headers.get("Retry-After")
    if raw is not None:
        try:
            return min(float(raw), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    reset_raw = r.headers.get("X-RateLimit-Reset")
    if reset_raw is not None:
        try:
            delay = float(reset_raw) - time.time()
            if delay > 0:
                return min(delay, _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    if r.status_code == 429 or _is_secondary_rate_limit(r):
        return _MAX_RETRY_AFTER_SECONDS
    return 2**attempt


def _get_with_retry(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    # Same retry contract as GitHubClient.request: 3 attempts on connection errors, 429,
    # rate-limit 403, or 5xx.
    for attempt in range(3):
        try:
            r = client.get(url, headers=headers)
        except httpx.RequestError:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise
        if (r.status_code == 429 or _is_secondary_rate_limit(r) or r.status_code >= 500) and attempt < 2:
            time.sleep(_retry_delay_seconds(r, attempt))
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


def _get_all_pages(base_url: str, path: str, token: str, items_key: str | None = None) -> list:
    """Paginate a GitHub list endpoint.

    `items_key` is for endpoints like /installation/repositories that nest the array."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    results = []
    sep = "&" if "?" in path else "?"
    url: str | None = f"{base_url}{path}{sep}per_page=100"
    pages_fetched = 0
    with httpx.Client(timeout=20) as client:
        while url:
            r = _get_with_retry(client, url, headers)
            r.raise_for_status()
            body = r.json()
            results.extend(body[items_key] if items_key else body)
            pages_fetched += 1
            url = None
            for part in r.headers.get("Link", "").split(","):
                part = part.strip()
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
            if url and pages_fetched >= _MAX_PAGES:
                logger.warning(
                    "Truncating pagination for %s after %d pages (%d items) -- more pages were available",
                    path,
                    pages_fetched,
                    len(results),
                )
                break
    return results


def _branch_protection_status(exc: httpx.HTTPStatusError) -> str:
    code = exc.response.status_code
    if code == 404:
        return "unprotected"
    # 403/429/5xx mean status can't be evaluated; treating them as "unprotected" would
    # fail the whole check on a flaky GitHub response.
    return "unknown"


def _feature_disabled(exc: httpx.HTTPStatusError) -> bool:
    """A 403 that means the alert feature is off for the repo (e.g. "Dependabot alerts are
    disabled for this repository", "Advanced Security must be enabled"), as opposed to a
    missing permission -- the former is a real "no alerts", the latter an unknown."""
    if exc.response.status_code != 403:
        return False
    try:
        message = str(exc.response.json().get("message", "")).lower()
    except ValueError:
        return False
    return "disabled" in message or "not enabled" in message or "must be enabled" in message


def _has_non_fast_forward_rule(base_url: str, owner: str, repo: str, branch: str, token: str) -> bool:
    """True when a repository ruleset blocks force pushes on ``branch`` (rulesets don't
    show up in the classic /protection endpoint)."""
    try:
        rules = _get(f"{base_url}/repos/{owner}/{repo}/rules/branches/{branch}", token)
    except httpx.HTTPError:
        return False
    return any(isinstance(r, dict) and r.get("type") == "non_fast_forward" for r in rules or [])


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
        account_type: str = "Organization",
    ) -> dict:
        if account_type == "User":
            # No MFA-requirement setting exists for personal accounts, and an installation
            # token can't read /user's 2FA status, so exclude from scoring.
            return {
                "status": "not_applicable",
                "value": "MFA requirement doesn't apply to personal accounts",
            }
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
        account_type: str = "Organization",  # unused — these checks are already per-repo
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
        account_type: str = "Organization",  # unused — these checks are already per-repo
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
        account_type: str = "Organization",  # unused — these checks are already per-repo
    ) -> dict:
        if repos is None:
            repos = _get_all_pages(base_url, f"/orgs/{owner}/repos", token)
        if len(repos) == 0:
            return {"status": "not_applicable", "value": {"critical": 0, "high": 0, "medium": 0, "low": 0}}
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        forbidden = 0
        disabled = 0
        for repo in repos:
            try:
                alerts = _get(
                    f"{base_url}/repos/{owner}/{repo['name']}/dependabot/alerts?state=open", token
                )
            except httpx.HTTPStatusError as exc:
                # 404 = alerts disabled (a real "no alerts"). 403 = missing scope, so the
                # count is unknown, not zero.
                if exc.response.status_code == 404:
                    continue
                if _feature_disabled(exc):
                    disabled += 1
                    continue
                if exc.response.status_code == 403:
                    forbidden += 1
                    continue
                raise
            for alert in alerts:
                severity = (alert.get("security_advisory") or {}).get("severity")
                if severity in counts:
                    counts[severity] += 1
        if disabled == len(repos):
            return {"status": "not_applicable", "value": counts}
        if forbidden and forbidden + disabled == len(repos):
            return {"status": "error", "value": counts}
        compliant = counts["critical"] == 0 and counts["high"] == 0
        if compliant and forbidden > 0:
            # A clean result from only the reachable repos must not be reported as "pass";
            # a "fail" from visible repos stays valid.
            return {"status": "error", "value": counts}
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
        account_type: str = "Organization",  # unused — these checks are already per-repo
    ) -> dict:
        if repos is None:
            repos = _get_all_pages(base_url, f"/orgs/{owner}/repos", token)
        total_repos = len(repos)
        if total_repos == 0:
            return {"status": "not_applicable", "value": {"open": 0, "repos_with_alerts": 0, "total_repos": 0}}
        open_count = 0
        repos_with_alerts = 0
        forbidden = 0
        disabled = 0
        for repo in repos:
            try:
                alerts = _get(
                    f"{base_url}/repos/{owner}/{repo['name']}/code-scanning/alerts?state=open", token
                )
            except httpx.HTTPStatusError as exc:
                # 404 = code scanning not enabled (a real "no alerts"). 403 = no access,
                # so the count is unknown, not zero.
                if exc.response.status_code == 404:
                    continue
                if _feature_disabled(exc):
                    disabled += 1
                    continue
                if exc.response.status_code == 403:
                    forbidden += 1
                    continue
                raise
            if alerts:
                repos_with_alerts += 1
                open_count += len(alerts)
        value = {"open": open_count, "repos_with_alerts": repos_with_alerts, "total_repos": total_repos}
        if disabled == total_repos:
            return {"status": "not_applicable", "value": value}
        if forbidden and forbidden + disabled == total_repos:
            return {"status": "error", "value": value}
        compliant = open_count == 0
        if compliant and forbidden > 0:
            # Same as DependabotAlertsCheck: don't report "pass" while some repos are unknown.
            return {"status": "error", "value": value}
        return {"status": "pass" if compliant else "fail", "value": value}


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
        account_type: str = "Organization",  # unused — these checks are already per-repo
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
                # The plain /branches/{branch} response never includes
                # `allow_force_pushes`; only the protection sub-resource does.
                details = _get(
                    f"{base_url}/repos/{owner}/{repo['name']}/branches/{branch}/protection", token
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    # 404 = no classic protection. A ruleset may still block force pushes;
                    # otherwise they're allowed (not unknown).
                    checked += 1
                    if not _has_non_fast_forward_rule(base_url, owner, repo["name"], branch, token):
                        force_push_allowed += 1
                    continue
                # 403/429: can't evaluate, so exclude from the denominator.
                unknown += 1
                continue
            except httpx.HTTPError:
                unknown += 1
                continue
            checked += 1
            allow_force_pushes = (details.get("allow_force_pushes") or {}).get("enabled")
            if allow_force_pushes:
                force_push_allowed += 1
        if checked == 0:
            return {"status": "error", "value": {"repos_checked": checked, "force_push_allowed": force_push_allowed}}
        return {
            "status": "pass" if force_push_allowed == 0 else "fail",
            "value": {"repos_checked": checked, "force_push_allowed": force_push_allowed},
        }
