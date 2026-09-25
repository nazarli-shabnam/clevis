"""Bulk default-branch protection apply across an org's repos.

Org-admin only. dry_run=true returns a per-repo diff; dry_run=false applies it,
capturing per-repo failures so one inaccessible repo doesn't abort the rest. Requires
GitHub's ``Administration: Read and write`` permission.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, automation_settings_repo
from src.services import branch_protection_bulk
from src.services.github_client import GitHubClient
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

_FEATURE = "branch_protection"
_PERMISSION_HINT = (
    "GitHub returned 403 for every repo. The most likely cause is a missing scope — "
    "reading and writing branch protection needs the repository 'Administration' "
    "permission at Read and write on Clevis's GitHub App (or the pasted token). If the "
    "App already has it, re-approve the installation. See docs/self-hosting.md."
)


class BulkRequest(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=500)
    preset: dict | None = None
    dry_run: bool = True
    save_preset: bool = False
    token: str | None = None


class RepoDiffOut(BaseModel):
    repo: str
    branch: str
    currently_protected: bool
    would_change: bool
    changes: dict
    error: str | None = None


class RepoResultOut(BaseModel):
    repo: str
    applied: bool
    error: str | None = None


class BulkDryRunResponse(BaseModel):
    dry_run: bool = True
    diffs: list[RepoDiffOut]


class BulkApplyResponse(BaseModel):
    dry_run: bool = False
    results: list[RepoResultOut]


def _all_forbidden(errors: list[str | None]) -> bool:
    real = [e for e in errors if e]
    return bool(real) and len(real) == len(errors) and all("403" in e for e in real)


@router.get("/orgs/{org_login}/branch-protection/preset")
def get_saved_preset(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
) -> dict:
    """The most recently saved preset for this org (flattened knobs), or ``{"preset": null}``.
    Lets the Automation card start from what was last applied instead of hard-coded defaults."""
    rows = automation_settings_repo.list_for_feature(db, ctx.org.tenant_id, _FEATURE)
    latest = max((r for r in rows if r.extra), key=lambda r: r.updated_at, default=None)
    return {"preset": latest.extra if latest else None}


@router.post("/orgs/{org_login}/branch-protection/bulk")
def bulk_branch_protection(
    org_login: str,
    body: BulkRequest,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    # require_org_role already ran set_tenant_session_context for this org's tenant.
    try:
        token = resolve_org_token(
            db,
            org_id=ctx.org.id,
            account_login=ctx.org.github_login,
            client_token=body.token or x_github_token,
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        branch_protection_bulk.normalize_preset(body.preset)
    except branch_protection_bulk.PresetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    owner = ctx.org.github_login
    action = "branch_protection.bulk_dryrun" if body.dry_run else "branch_protection.bulk_apply"
    audit_repo.write(
        db, user.email, action, owner,
        {"repos": body.repos, "dry_run": body.dry_run}, tenant_id=ctx.org.tenant_id,
    )

    # plan_bulk / apply_bulk capture every httpx error per repo -- a whole-batch failure
    # surfaces as every result carrying an error, which _all_forbidden turns into the hint.
    client = GitHubClient(token)
    if body.dry_run:
        diffs = branch_protection_bulk.plan_bulk(client, owner, body.repos, body.preset)
        if _all_forbidden([d.error for d in diffs]):
            raise HTTPException(status_code=400, detail=_PERMISSION_HINT)
        return BulkDryRunResponse(
            diffs=[
                RepoDiffOut(
                    repo=d.repo, branch=d.branch, currently_protected=d.currently_protected,
                    would_change=d.would_change, changes=d.changes, error=d.error,
                )
                for d in diffs
            ]
        )

    results = branch_protection_bulk.apply_bulk(client, owner, body.repos, body.preset)
    if _all_forbidden([r.error for r in results]):
        raise HTTPException(status_code=400, detail=_PERMISSION_HINT)

    if body.save_preset:
        preset = branch_protection_bulk.normalize_preset(body.preset)
        for r in results:
            if r.applied:
                automation_settings_repo.upsert(
                    db, ctx.org.tenant_id, f"{owner}/{r.repo}", _FEATURE,
                    enabled=True, extra=preset,
                )
        db.commit()

    return BulkApplyResponse(
        results=[RepoResultOut(repo=r.repo, applied=r.applied, error=r.error) for r in results]
    )
