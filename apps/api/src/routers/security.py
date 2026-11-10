"""Security compliance matrix and secret-scanning alerts, per-repo (not org-wide like
`analytics.overview`) -- re-derives each dimension directly from the GitHub API per
repo since `checks.github_checks` aggregates across all repos into one org-wide result.

Personal-scoped (`/me/...`); the Security page scans an arbitrary owner by name, so
token resolution goes through `resolve_owner_token` rather than `require_org_role`.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.auth import UserOut, require_auth
from src.core.db import SecurityAlert, SecurityAlertSync, get_db
from src.core.rbac import OrgContext, require_org_role, set_tenant_session_context
from src.repositories import installation_repo, org_repo, tenant_repo
from src.schemas.security import (
    DependabotBurndownResponse,
    MatrixSummary,
    RepoSecurityRow,
    SecretAlert,
    SecretScanningResponse,
    SecurityMatrixResponse,
    VulnCounts,
)
from src.services import dependabot_burndown
from src.services.analytics_service import get_account_type
from src.services.github_client import GitHubClient, github_error as _github_error, list_owner_repos
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_owner_token

router = APIRouter()

_SLA_DEFAULTS = {"critical": 7, "high": 30}

# Each repo costs up to 3 additional GitHub calls (branch, dependabot, code-scanning),
# so the cap here is tighter than the single-call aggregate helpers in analytics.py.
_MAX_REPOS_FOR_MATRIX = 20


def _branch_lookup_is_unknown(exc: httpx.HTTPStatusError) -> bool:
    # Only a 404 is a real negative answer ("unprotected"). Every other failure -- 401/403/429
    # (the token can't see the answer) and 5xx (GitHub had a problem) -- says nothing about the
    # repo, and must not be scored as a compliance fail.
    return exc.response.status_code != 404


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
    everything up front and hands each worker a plain dict slice instead of `db` itself.

    Only (repo, kind) pairs the alert backfill read in full (security_alert_syncs) are returned: before
    that, the rows are whatever webhooks happened to deliver, so a repo with a few of them is not a
    repo whose alerts are all known, and the caller must ask GitHub instead."""
    if not repo_full_names:
        return {}
    synced = {
        (s.repo, s.kind)
        for s in db.query(SecurityAlertSync.repo, SecurityAlertSync.kind).filter(
            SecurityAlertSync.tenant_id == tenant_id, SecurityAlertSync.repo.in_(repo_full_names)
        )
    }
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
        if (row.repo, row.kind) in synced:
            by_repo.setdefault(row.repo, []).append(row)
    return by_repo


def _dependabot_from_aggregate(dependabot_rows: list) -> dict:
    """dependabot dimension from security_alerts rows filtered to kind=='dependabot',
    instead of a live GitHub call. Considers every state for dependabot_enabled -- a repo
    whose alerts are all dismissed still has Dependabot enabled. critical_count/high_count
    only count 'open' rows, since only a currently-open alert is a live finding.

    Caller only reaches this with a non-empty dependabot_rows, which _open_alerts_by_repo returns only for a
    repo whose Dependabot alerts the backfill read in full (security_alert_syncs); any other repo falls back to
    the live GitHub path. A repo with zero rows also takes it: zero rows cannot tell "Dependabot off" from
    "enabled, no alerts". Gated independently of code_scanning rows, since one kind can be synced while the
    other isn't."""
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


def _force_push_allowed(client: GitHubClient, owner: str, name: str, branch: str) -> bool:
    """Whether the default branch accepts force pushes. Mirrors ``DefaultBranchNoForcePushCheck``.

    ``GET /branches/{branch}`` never carries ``allow_force_pushes``, so ask the protection
    sub-resource (404 = no classic protection, which allows them) and, where classic protection
    does not already forbid them, let an active ruleset's ``non_fast_forward`` rule veto it.
    Any other failure raises: the caller must treat it as unknown, not as compliant."""
    try:
        protection = client.request("GET", f"/repos/{owner}/{name}/branches/{branch}/protection")
        if not (isinstance(protection, dict) and (protection.get("allow_force_pushes") or {}).get("enabled")):
            return False
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
    rules = client.request_paginated(f"/repos/{owner}/{name}/rules/branches/{branch}")
    return not any(isinstance(r, dict) and r.get("type") == "non_fast_forward" for r in rules)


def _repo_row(client: GitHubClient, owner: str, repo: dict, alerts_by_repo: dict[str, list] | None = None) -> RepoSecurityRow:
    name = repo["name"]
    branch = repo.get("default_branch")
    unknown: list[str] = []

    branch_protection = False
    force_push_allowed = False
    branch_found = False
    if not branch:
        # The repo payload carries no default branch: there is nothing to ask GitHub about, and
        # requesting /branches/None would just be a guaranteed 404 read as "unprotected".
        unknown.extend(["branch_protection", "force_push"])
    else:
        try:
            details = client.request("GET", f"/repos/{owner}/{name}/branches/{branch}")
            if isinstance(details, dict):
                branch_protection = bool(details.get("protected"))
            branch_found = True
        except httpx.HTTPStatusError as exc:
            # Force push is looked up only once the branch is known to exist, so an unknown
            # branch answer means force_push is equally unknown.
            if _branch_lookup_is_unknown(exc):
                unknown.extend(["branch_protection", "force_push"])
        except httpx.RequestError:
            # A transient network error is exactly as unknowable as a 403/429 -- neither
            # is a real "unprotected" answer from GitHub.
            unknown.extend(["branch_protection", "force_push"])
        if branch_found:
            try:
                force_push_allowed = _force_push_allowed(client, owner, name, branch)
            except (httpx.HTTPStatusError, httpx.RequestError):
                unknown.append("force_push")

    secret_scanning = (
        (repo.get("security_and_analysis") or {}).get("secret_scanning") or {}
    ).get("status") == "enabled"

    alerts_source = "github"
    # Rows arrive here only for a (repo, kind) the alert backfill read in full (see _open_alerts_by_repo);
    # otherwise the repo is asked live. Gated per-repo AND per-kind: one kind can be synced while the other isn't.
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
        # A repo with an unevaluated dimension (lookup failed, no default branch) is not known to be
        # compliant, even if everything that could be checked passed.
        fully_compliant_count=sum(1 for r in rows if r.score == 100 and not r.unknown_dimensions),
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


# GitHub logins and repo names; "." and ".." are path segments, not names, so they must not reach the URL.
_GH_NAME_RE = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._-]{1,100}$")


@router.get("/me/analytics/security-matrix/{owner}/{repo}", response_model=RepoSecurityRow)
def personal_repo_security_row(
    owner: str,
    repo: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    """One matrix row for a single repo -- what the org matrix shows for that cell, without
    scanning every repo in the org to get it. Same token resolution and aggregate-vs-live rules."""
    if not _GH_NAME_RE.match(owner) or not _GH_NAME_RE.match(repo):
        raise HTTPException(status_code=422, detail="Invalid owner or repository name")
    try:
        token = resolve_owner_token(db, user_id=user.id, owner=owner, client_token=x_github_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    tenant_id = _security_connected_tenant(db, user.id, owner)
    client = GitHubClient(token)
    try:
        repo_meta = client.request("GET", f"/repos/{owner}/{repo}")
        if not isinstance(repo_meta, dict) or not isinstance(repo_meta.get("name"), str):
            raise HTTPException(status_code=502, detail="Unexpected response from GitHub")
        alerts_by_repo = None
        if tenant_id is not None:
            alerts_by_repo = _open_alerts_by_repo(db, tenant_id, [f"{owner}/{repo_meta['name']}"])
        return _repo_row(client, owner, repo_meta, alerts_by_repo)
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


def _sla_days() -> dict[str, int | None]:
    """Critical/high SLA windows from instance config; a bad stored value falls back to the default."""
    out: dict[str, int | None] = {}
    for severity, default in _SLA_DEFAULTS.items():
        try:
            value = int(get_config(f"dependabot_sla_{severity}_days", str(default)))
        except ValueError:
            value = default
        out[severity] = value if value >= 1 else default
    return out


@router.get("/orgs/{org_login}/security/dependabot-burndown", response_model=DependabotBurndownResponse)
def org_dependabot_burndown(
    days: int = 30,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(
            SecurityAlert.repo,
            SecurityAlert.number,
            SecurityAlert.state,
            SecurityAlert.severity,
            SecurityAlert.created_at,
            SecurityAlert.updated_at,
        )
        .filter(SecurityAlert.tenant_id == ctx.org.tenant_id, SecurityAlert.kind == "dependabot")
        .all()
    )
    return dependabot_burndown.compute(rows, datetime.now(timezone.utc), days, _sla_days())
