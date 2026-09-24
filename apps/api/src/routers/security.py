"""Security compliance matrix and secret-scanning alerts, per-repo (not org-wide like
`analytics.overview`) -- re-derives each dimension directly from the GitHub API per
repo since `checks.github_checks` aggregates across all repos into one org-wide result.

Personal-scoped (`/me/...`); the Security page scans an arbitrary owner by name, so
token resolution goes through `resolve_owner_token` rather than `require_org_role`.
"""

from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import SecurityAlert, get_db
from src.core.rbac import set_tenant_session_context
from src.repositories import installation_repo, org_repo, tenant_repo
from src.schemas.security import (
    MatrixSummary,
    RepoSecurityRow,
    SecretAlert,
    SecretScanningResponse,
    SecurityMatrixResponse,
    VulnCounts,
)
from src.services.analytics_service import get_account_type
from src.services.github_client import GitHubClient, github_error as _github_error, list_owner_repos
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_owner_token

router = APIRouter()

# Each repo costs up to 3 additional GitHub calls (branch, dependabot, code-scanning),
# so the cap here is tighter than the single-call aggregate helpers in analytics.py.
_MAX_REPOS_FOR_MATRIX = 20


def _branch_protection_status(exc: httpx.HTTPStatusError) -> str:
    # A 404 is a real negative answer ("unprotected"), not an error; 403/429 mean the
    # token can't see the answer at all, which must not be scored as a compliance fail.
    code = exc.response.status_code
    if code == 404:
        return "unprotected"
    if code in (403, 429):
        return "unknown"
    return "unprotected"


def _security_connected_tenant(db: Session, user_id: int, owner: str) -> int | None:
    """Mirrors analytics.py's _cockpit_connected_tenant: same personal-endpoint membership
    check, installation_id-presence gate, and RLS session-context setup. Duplicated rather
    than imported -- routers don't import each other's private helpers in this codebase."""
    org = org_repo.get_by_login_ci(db, owner)
    if org is None:
        return None
    org = org_repo.ensure_tenant_linked(db, org)
    if tenant_repo.get_membership(db, org.tenant_id, user_id) is None:
        return None
    installation = installation_repo.get_for_org(db, org_id=org.id, account_login=owner)
    if installation is None or installation.installation_id is None:
        return None
    set_tenant_session_context(db, org.tenant_id, user_id)
    return org.tenant_id


def _open_alerts_by_repo(db: Session, tenant_id: int, repo_full_names: list[str]) -> dict[str, list]:
    """One query for every scanned repo's dependabot/code_scanning security_alerts rows
    (every state, not just open), not one query per repo. Also avoids sharing the
    SQLAlchemy Session across threads: _repo_row runs concurrently, so this pre-fetches
    everything up front and hands each worker a plain dict slice instead of `db` itself."""
    if not repo_full_names:
        return {}
    rows = (
        db.query(SecurityAlert.repo, SecurityAlert.kind, SecurityAlert.severity, SecurityAlert.state)
        .filter(
            SecurityAlert.tenant_id == tenant_id,
            SecurityAlert.repo.in_(repo_full_names),
            SecurityAlert.kind.in_(("dependabot", "code_scanning")),
        )
        .all()
    )
    by_repo: dict[str, list] = {}
    for row in rows:
        by_repo.setdefault(row.repo, []).append(row)
    return by_repo


def _dependabot_from_aggregate(dependabot_rows: list) -> dict:
    """dependabot dimension from security_alerts rows filtered to kind=='dependabot',
    instead of a live GitHub call. Considers every state for dependabot_enabled -- a repo
    whose alerts are all dismissed still has Dependabot enabled. critical_count/high_count
    only count 'open' rows, since only a currently-open alert is a live finding.

    Caller only reaches this with a non-empty dependabot_rows -- a repo with zero ingested
    rows falls back to the live GitHub path instead, since security_alerts has no
    completeness cursor to distinguish "no alerts ever" from "not yet ingested". Gated
    independently of code_scanning rows, since one kind can be ingested while the other isn't."""
    open_rows = [r for r in dependabot_rows if r.state == "open"]
    return {
        "dependabot_enabled": len(dependabot_rows) > 0,
        "critical_count": sum(1 for r in open_rows if r.severity == "critical"),
        "high_count": sum(1 for r in open_rows if r.severity == "high"),
    }


def _code_scanning_clear_from_aggregate(code_scanning_rows: list) -> bool:
    """code_scanning dimension from security_alerts rows already filtered to
    kind=='code_scanning'. Same completeness/per-kind reasoning as
    _dependabot_from_aggregate's docstring -- caller only reaches this with a non-empty
    code_scanning_rows."""
    return not any(r.state == "open" for r in code_scanning_rows)


def _repo_row(client: GitHubClient, owner: str, repo: dict, alerts_by_repo: dict[str, list] | None = None) -> RepoSecurityRow:
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
        # force_push_allowed comes from the same branch-details response, so an unknown
        # branch_protection answer means force_push is equally unknown.
        if _branch_protection_status(exc) == "unknown":
            unknown.extend(["branch_protection", "force_push"])
    except httpx.RequestError:
        # A transient network error is exactly as unknowable as a 403/429 -- neither
        # is a real "unprotected" answer from GitHub.
        unknown.extend(["branch_protection", "force_push"])

    secret_scanning = (
        (repo.get("security_and_analysis") or {}).get("secret_scanning") or {}
    ).get("status") == "enabled"

    alerts_source = "github"
    # security_alerts has no backfill/sync-cursor -- only webhook events populate it, so a
    # repo with zero rows here is ambiguous ("no alerts ever" vs. "not yet ingested"). Gated
    # per-repo AND per-kind: one kind can be ingested for a repo while the other isn't yet.
    repo_alert_rows = alerts_by_repo.get(f"{owner}/{name}", []) if alerts_by_repo is not None else []
    dependabot_alert_rows = [r for r in repo_alert_rows if r.kind == "dependabot"]
    code_scanning_alert_rows = [r for r in repo_alert_rows if r.kind == "code_scanning"]

    if dependabot_alert_rows:
        alerts_source = "aggregate"
        aggregate = _dependabot_from_aggregate(dependabot_alert_rows)
        dependabot_enabled = aggregate["dependabot_enabled"]
        critical_count = aggregate["critical_count"]
        high_count = aggregate["high_count"]
    else:
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
            # 404 means Dependabot alerts are genuinely disabled -- a real "no alerts"
            # answer. Any other status means the count is unknown, not zero.
            if exc.response.status_code != 404:
                unknown.append("dependabot")
        except httpx.RequestError:
            unknown.append("dependabot")

    if code_scanning_alert_rows:
        alerts_source = "aggregate"
        code_scanning_clear = _code_scanning_clear_from_aggregate(code_scanning_alert_rows)
    else:
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
        alerts_source=alerts_source,
    )


def _build_matrix(
    owner: str, token: str, db: Session | None = None, tenant_id: int | None = None, account_type: str = "Organization"
) -> SecurityMatrixResponse:
    client = GitHubClient(token)
    repos = list_owner_repos(client, owner, account_type)
    scanned = repos[:_MAX_REPOS_FOR_MATRIX]

    alerts_by_repo = None
    if db is not None and tenant_id is not None:
        alerts_by_repo = _open_alerts_by_repo(db, tenant_id, [f"{owner}/{r['name']}" for r in scanned])

    with ThreadPoolExecutor(max_workers=10) as pool:
        rows = list(pool.map(lambda r: _repo_row(client, owner, r, alerts_by_repo), scanned))

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
    tenant_id = _security_connected_tenant(db, user.id, owner)
    try:
        account_type = get_account_type(owner, token)
        return _build_matrix(owner, token, db=db, tenant_id=tenant_id, account_type=account_type)
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc


def _secret_scanning_alerts_from_aggregate(db: Session, tenant_id: int, owner: str, repo: str) -> list[SecretAlert]:
    """Reads security_alerts instead of a live GitHub call. No `url` field is stored (only
    the live API response carries html_url) -- None, not "" (a fake link); the UI renders
    a plain non-link row when url is missing."""
    rows = (
        db.query(SecurityAlert)
        .filter(
            SecurityAlert.tenant_id == tenant_id,
            SecurityAlert.repo == f"{owner}/{repo}",
            SecurityAlert.kind == "secret_scanning",
        )
        .all()
    )
    return [
        SecretAlert(
            number=row.number,
            state=row.state,
            secret_type=row.details.get("secret_type", ""),
            secret_type_display=row.details.get("secret_type_display_name", row.details.get("secret_type", "")),
            resolved_reason=row.details.get("resolution"),
            created_at=row.created_at,
            resolved_at=row.updated_at if row.state != "open" else None,
            repo=f"{owner}/{repo}",
            url=None,
        )
        for row in rows
    ]


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

    tenant_id = _security_connected_tenant(db, user.id, owner)
    if tenant_id is not None:
        alerts = _secret_scanning_alerts_from_aggregate(db, tenant_id, owner, repo)
        # security_alerts has no backfill/sync-cursor -- only webhook events populate it, so
        # a zero-row result is ambiguous ("no alerts" vs "not yet ingested") and can't be
        # trusted as authoritative -- fall back to the live call rather than under-report.
        if alerts:
            return SecretScanningResponse(repository=f"{owner}/{repo}", alerts=alerts, source="aggregate")

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
    return SecretScanningResponse(repository=f"{owner}/{repo}", alerts=alerts, source="github")
