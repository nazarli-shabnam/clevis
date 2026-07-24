"""Security compliance matrix and secret-scanning alerts (docs/plan.md Phase 16).

The org-wide `analytics.overview` scan already computes org-level pass/fail check
results (see `checks.runner.run_all_checks`) -- this router instead breaks the same
dimensions down per-repo, which is what an admin needs to act on a specific finding.
It re-derives each dimension directly from the GitHub API per repo rather than reusing
`checks.github_checks`, since those check classes are written to aggregate across all
repos into a single org-wide pass/fail, not to return a per-repo row.

Personal-scoped (`/me/...`), matching `analytics.py`'s `/me/analytics/overview` --
the Security page scans an arbitrary owner by name, not necessarily a workspace Org
the caller has an `OrgMembership` row for, so there's no `require_org_role` path
gating this route directly. Token resolution still prefers an org-scoped installation
over a personal one when `owner` does match an Org the caller is a member of, via
`resolve_owner_token` -- see its docstring for why that's still membership-gated.
"""

from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.schemas.security import (
    MatrixSummary,
    RepoSecurityRow,
    SecretAlert,
    SecretScanningResponse,
    SecurityMatrixResponse,
    VulnCounts,
)
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_owner_token

router = APIRouter()

# Each repo costs up to 3 additional GitHub calls (branch, dependabot, code-scanning)
# on top of the initial repo list, so the cap here is tighter than the single-call
# aggregate helpers in analytics.py.
_MAX_REPOS_FOR_MATRIX = 20


def _branch_protection_status(exc: httpx.HTTPStatusError) -> str:
    # Mirrors packages/checks/src/checks/github_checks.py's _branch_protection_status:
    # a 404 is a real negative answer ("unprotected"), not an error; 403/429 mean the
    # token can't see the answer at all, which must not be scored as a compliance fail.
    code = exc.response.status_code
    if code == 404:
        return "unprotected"
    if code in (403, 429):
        return "unknown"
    return "unprotected"


def _repo_row(client: GitHubClient, owner: str, repo: dict) -> RepoSecurityRow:
    name = repo["name"]
    branch = repo.get("default_branch")
    unknown: list[str] = []

    branch_protection = False
    force_push_allowed = False
    try:
        details = client.request("GET", f"/repos/{owner}/{name}/branches/{branch}")
        if isinstance(details, dict):
            branch_protection = bool(details.get("protected"))
            protection = details.get("protection") or {}
            force_push_allowed = bool((protection.get("allow_force_pushes") or {}).get("enabled"))
    except httpx.HTTPStatusError as exc:
        # force_push_allowed comes from the same branch-details response, so an
        # unknown branch_protection answer means force_push is equally unknown --
        # they must not resolve to opposite compliance verdicts from one failed call.
        if _branch_protection_status(exc) == "unknown":
            unknown.extend(["branch_protection", "force_push"])
    except httpx.RequestError:
        # A transient network error is exactly as unknowable as a 403/429 -- neither
        # is a real "unprotected" answer from GitHub (see PR history: 10b10e9, 027d30b).
        unknown.extend(["branch_protection", "force_push"])

    secret_scanning = (
        (repo.get("security_and_analysis") or {}).get("secret_scanning") or {}
    ).get("status") == "enabled"

    dependabot_enabled = False
    critical_count = 0
    high_count = 0
    try:
        alerts = client.request("GET", f"/repos/{owner}/{name}/dependabot/alerts", params={"state": "open"})
        dependabot_enabled = True
        if isinstance(alerts, list):
            for alert in alerts:
                severity = (alert.get("security_advisory") or {}).get("severity")
                if severity == "critical":
                    critical_count += 1
                elif severity == "high":
                    high_count += 1
    except httpx.HTTPStatusError as exc:
        # 404 means Dependabot alerts are genuinely disabled for this repo -- a real
        # "no alerts" answer. Any other status (403 missing security-events scope,
        # 429, ...) means the alert count is unknown, not zero, so it must not
        # silently score as "no critical/high alerts" -- see the identical fix in
        # DependabotAlertsCheck (packages/checks/src/checks/github_checks.py, 3184c76).
        if exc.response.status_code != 404:
            unknown.append("dependabot")
    except httpx.RequestError:
        unknown.append("dependabot")

    code_scanning_clear = True
    try:
        cs_alerts = client.request("GET", f"/repos/{owner}/{name}/code-scanning/alerts", params={"state": "open"})
        if isinstance(cs_alerts, list):
            code_scanning_clear = len(cs_alerts) == 0
    except httpx.HTTPStatusError as exc:
        # Same 404-vs-other distinction as Dependabot above: 404 is a genuine
        # "disabled, so no alerts" answer; anything else means no visibility.
        if exc.response.status_code != 404:
            unknown.append("code_scanning")
    except httpx.RequestError:
        unknown.append("code_scanning")

    dimensions = {
        "branch_protection": branch_protection,
        "secret_scanning": secret_scanning,
        "dependabot": critical_count == 0 and high_count == 0,
        "code_scanning": code_scanning_clear,
        "force_push": not force_push_allowed,
    }
    evaluable = {k: v for k, v in dimensions.items() if k not in unknown}
    score = round(100 * sum(evaluable.values()) / len(evaluable)) if evaluable else 0

    return RepoSecurityRow(
        repo=name,
        branch_protection=branch_protection,
        secret_scanning=secret_scanning,
        dependabot_enabled=dependabot_enabled,
        dependabot_critical_count=critical_count,
        dependabot_high_count=high_count,
        code_scanning=code_scanning_clear,
        force_push_allowed=force_push_allowed,
        score=score,
        unknown_dimensions=unknown,
    )


def _build_matrix(owner: str, token: str) -> SecurityMatrixResponse:
    client = GitHubClient(token)
    repos = client.request_paginated(f"/orgs/{owner}/repos", params={"type": "all", "sort": "pushed"})
    scanned = repos[:_MAX_REPOS_FOR_MATRIX]

    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(lambda r: _repo_row(client, owner, r), scanned))

    vuln = VulnCounts(
        critical=sum(r.dependabot_critical_count for r in rows),
        high=sum(r.dependabot_high_count for r in rows),
        medium=0,
        low=0,
    )
    summary = MatrixSummary(
        fully_compliant_count=sum(1 for r in rows if r.score == 100),
        critical_risk_count=sum(1 for r in rows if r.dependabot_critical_count > 0),
        secret_hits_count=sum(1 for r in rows if not r.secret_scanning),
        vuln_by_severity=vuln,
    )
    return SecurityMatrixResponse(owner=owner, repos=rows, summary=summary)


@router.get("/me/analytics/security-matrix/{owner}", response_model=SecurityMatrixResponse)
def personal_security_matrix(
    owner: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    try:
        token = resolve_owner_token(db, user_id=user.id, owner=owner, client_token=x_github_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return _build_matrix(owner, token)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


@router.get("/me/repos/{owner}/{repo}/secret-scanning", response_model=SecretScanningResponse)
def personal_secret_scanning(
    owner: str,
    repo: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    try:
        token = resolve_owner_token(db, user_id=user.id, owner=owner, client_token=x_github_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    client = GitHubClient(token)
    try:
        raw = client.request("GET", f"/repos/{owner}/{repo}/secret-scanning/alerts", params={"per_page": 50})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    alerts = [
        SecretAlert(
            number=a["number"],
            state=a.get("state", "open"),
            secret_type=a.get("secret_type", ""),
            # GitHub's actual field name is secret_type_display_name, not secret_type_display.
            secret_type_display=a.get("secret_type_display_name", a.get("secret_type", "")),
            resolved_reason=a.get("resolution"),
            created_at=a["created_at"],
            resolved_at=a.get("resolved_at"),
            repo=f"{owner}/{repo}",
            url=a.get("html_url", ""),
        )
        for a in raw
        if isinstance(a, dict) and "number" in a and "created_at" in a
    ]
    return SecretScanningResponse(repository=f"{owner}/{repo}", alerts=alerts)
