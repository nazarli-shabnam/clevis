"""Dependabot auto-triage endpoints: per-repo opt-in/mode (PUT) and sweep run (POST,
org-admin only, dry_run makes no GitHub writes). Every decision is audit-logged.
Approving needs ``pull_requests: write``; merging needs ``contents: write``.
"""

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import audit_repo, automation_settings_repo
from src.services import dependabot_triage
from src.services.github_client import GitHubClient
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

_FEATURE = "dependabot_triage"
_PERMISSION_HINT = (
    "GitHub returned 403. Auto-triage needs Clevis's GitHub App (or token) to have the "
    "repository 'Pull requests' permission at Read and write (to approve) and, for "
    "approve-and-merge mode, 'Contents' at Read and write (to merge), plus 'Checks' and "
    "'Commit statuses' at Read-only (to verify CI is green). See docs/self-hosting.md."
)
_MERGE_METHODS = ("merge", "squash", "rebase")


class SettingRequest(BaseModel):
    enabled: bool
    mode: str = dependabot_triage.MODE_APPROVE_ONLY
    merge_method: str = "squash"


class TriageRequest(BaseModel):
    token: str | None = None
    repos: list[str] | None = None
    dry_run: bool = False


class DecisionOut(BaseModel):
    repo: str
    number: int | None
    title: str
    action: str
    reason: str = ""


class TriageResponse(BaseModel):
    decisions: list[DecisionOut]


@router.get("/orgs/{org_login}/repos/{owner}/{repo}/automation/dependabot-triage")
def get_triage_setting(
    org_login: str,
    owner: str,
    repo: str,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    row = automation_settings_repo.get(db, ctx.org.tenant_id, f"{owner}/{repo}", _FEATURE)
    return {
        "enabled": bool(row and row.enabled),
        "mode": (row.mode if row and row.mode else dependabot_triage.MODE_APPROVE_ONLY),
        "merge_method": (row.extra or {}).get("merge_method", "squash") if row else "squash",
    }


@router.put("/orgs/{org_login}/repos/{owner}/{repo}/automation/dependabot-triage")
def set_triage_setting(
    org_login: str,
    owner: str,
    repo: str,
    body: SettingRequest,
    user: UserOut = Depends(require_auth),
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(owner, ctx)
    if body.mode not in dependabot_triage.MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of: {', '.join(dependabot_triage.MODES)}")
    if body.merge_method not in _MERGE_METHODS:
        raise HTTPException(status_code=422, detail=f"merge_method must be one of: {', '.join(_MERGE_METHODS)}")
    automation_settings_repo.upsert(
        db,
        ctx.org.tenant_id,
        f"{owner}/{repo}",
        _FEATURE,
        enabled=body.enabled,
        mode=body.mode,
        extra={"merge_method": body.merge_method},
    )
    db.commit()
    audit_repo.write(
        db, user.email, "dependabot_triage.setting_saved", f"{owner}/{repo}",
        {"enabled": body.enabled, "mode": body.mode, "merge_method": body.merge_method},
        tenant_id=ctx.org.tenant_id,
    )
    return {"enabled": body.enabled, "mode": body.mode, "merge_method": body.merge_method}


@router.post("/orgs/{org_login}/dependabot-triage", response_model=TriageResponse)
def run_triage(
    org_login: str,
    body: TriageRequest,
    user: UserOut = Depends(require_auth),
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
) -> TriageResponse:
    try:
        token = resolve_org_token(
            db,
            org_id=ctx.org.id,
            account_login=ctx.org.github_login,
            client_token=body.token or x_github_token,
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # GitHub repo names are case-insensitive; key and look up lowercased so "Acme/API" and
    # "acme/api" resolve to the same saved setting (and the same repo isn't run twice).
    settings = {
        s.repo.lower(): s
        for s in automation_settings_repo.list_for_feature(db, ctx.org.tenant_id, _FEATURE)
    }
    # `repos: null` (omitted) means "every configured repo"; an explicit `repos: []`
    # means "none" and must not expand to all.
    if body.repos is None:
        target = [s.repo for s in settings.values()]
    else:
        seen: set[str] = set()
        target = [r for r in body.repos if not (r.lower() in seen or seen.add(r.lower()))]
    audit_repo.write(
        db, user.email, "dependabot_triage.run", ctx.org.github_login,
        {"repos": target, "dry_run": body.dry_run}, tenant_id=ctx.org.tenant_id,
    )

    client = GitHubClient(token)
    out: list[DecisionOut] = []
    forbidden_repos = 0
    for full_repo in target:
        owner_r, _, name_r = full_repo.partition("/")
        if not name_r:
            owner_r, name_r = ctx.org.github_login, full_repo
        setting = settings.get(full_repo.lower()) or settings.get(f"{owner_r}/{name_r}".lower())
        try:
            decisions = dependabot_triage.triage(
                client,
                owner_r,
                name_r,
                enabled=bool(setting and setting.enabled),
                mode=(setting.mode if setting else dependabot_triage.MODE_APPROVE_ONLY),
                merge_method=((setting.extra or {}).get("merge_method", "squash") if setting else "squash"),
                dry_run=body.dry_run,
            )
        except httpx.HTTPStatusError as exc:
            # 403 is almost always a missing scope. Record it against this repo so earlier
            # repos' results (already acted on GitHub) are still returned; only if every repo
            # was forbidden does the whole call fail with the hint.
            if exc.response.status_code == 403:
                forbidden_repos += 1
                decisions = [dependabot_triage.Decision(None, "", "error", _PERMISSION_HINT)]
            else:
                decisions = [dependabot_triage.Decision(None, "", "error", f"GitHub API error: {exc.response.status_code}")]
        except httpx.RequestError:
            decisions = [dependabot_triage.Decision(None, "", "error", "GitHub API unreachable")]

        if not decisions and not (setting and setting.enabled):
            decisions = [dependabot_triage.Decision(None, "", "skipped", "not enabled for this repo")]

        for d in decisions:
            out.append(
                DecisionOut(repo=full_repo, number=d.number, title=d.title, action=d.action, reason=d.reason)
            )
            # audit_repo.write commits per call, so a decision already acted on GitHub is
            # never left unaudited by a later repo's failure.
            audit_repo.write(
                db, user.email, f"dependabot_triage.{d.action}",
                f"{full_repo}#{d.number}" if d.number else full_repo,
                {"reason": d.reason}, tenant_id=ctx.org.tenant_id,
            )
    if target and forbidden_repos == len(target):
        raise HTTPException(status_code=400, detail=_PERMISSION_HINT)
    return TriageResponse(decisions=out)
