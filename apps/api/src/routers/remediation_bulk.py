"""Apply one security-check fix to many repos of an org ("Fix N repos").

Org-admin only, like the other org-wide write automations (bulk branch protection). ``dry_run``
(the default) returns what each repo would get without writing anything; ``dry_run=false`` applies
it, capturing per-repo failures so one inaccessible repo doesn't abort the rest. Needs the same
GitHub write permissions as the single-repo "Fix this" (``Administration``).

A batch is capped at ``MAX_REPOS`` because it runs synchronously inside one request: a few GitHub
calls per repo, with their own retry back-off, must stay well inside a proxy's timeout.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo
from src.services import check_remediation, check_remediation_bulk
from src.services.github_client import GitHubClient
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

MAX_REPOS = 100

_PERMISSION_HINT = (
    "GitHub returned 403 for every repo. The most likely cause is a missing scope — this fix "
    "needs the repository 'Administration' permission at Read and write on Clevis's GitHub App "
    "(or the pasted token). If the App already has it, re-approve the installation. See "
    "docs/self-hosting.md."
)


class BulkRemediateRequest(BaseModel):
    check_id: str
    repos: list[str] = Field(min_length=1, max_length=MAX_REPOS)
    dry_run: bool = True
    token: str | None = None


class BulkRemediateItem(BaseModel):
    repo: str
    status: Literal["would_change", "unchanged", "applied", "failed"]
    detail: str = ""


class BulkRemediateResponse(BaseModel):
    check_id: str
    dry_run: bool
    items: list[BulkRemediateItem]
    # Set when every repo came back 403, which almost always means a missing GitHub permission. The
    # per-repo results are still returned: a 403 can also be one inaccessible or archived repo.
    hint: str | None = None


def _all_forbidden(items: list[check_remediation_bulk.BulkItem]) -> bool:
    # A single repo is not evidence of a missing App permission: it may just be archived or not
    # granted to the installation.
    failed = [i for i in items if i.status == "failed"]
    return len(items) > 1 and len(failed) == len(items) and all("403" in i.detail for i in failed)


@router.post("/orgs/{org_login}/security/remediate/bulk", response_model=BulkRemediateResponse)
def bulk_remediate(
    org_login: str,
    body: BulkRemediateRequest,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
) -> BulkRemediateResponse:
    if body.check_id not in check_remediation.supported_check_ids():
        raise HTTPException(status_code=404, detail=f"No automated fix for check {body.check_id!r}")

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

    owner = ctx.org.github_login
    # Recorded before anything reaches GitHub, so a rejected or aborted batch is still audited.
    audit_repo.write(
        db,
        user.email,
        "security.remediate.bulk_dryrun" if body.dry_run else "security.remediate.bulk_apply",
        owner,
        {"check_id": body.check_id, "repos": body.repos, "dry_run": body.dry_run},
        tenant_id=ctx.org.tenant_id,
    )

    client = GitHubClient(token)
    run = check_remediation_bulk.plan_bulk if body.dry_run else check_remediation_bulk.apply_bulk
    items = run(client, body.check_id, owner, body.repos)

    if not body.dry_run:
        # The row above only says what was asked for; this one says what happened, so an audit
        # can tell which repos were actually changed.
        audit_repo.write(
            db,
            user.email,
            "security.remediate.bulk_result",
            owner,
            {
                "check_id": body.check_id,
                "applied": [i.repo for i in items if i.status == "applied"],
                "unchanged": [i.repo for i in items if i.status == "unchanged"],
                "failed": {i.repo: i.detail for i in items if i.status == "failed"},
            },
            tenant_id=ctx.org.tenant_id,
        )

    return BulkRemediateResponse(
        check_id=body.check_id,
        dry_run=body.dry_run,
        items=[BulkRemediateItem(repo=i.repo, status=i.status, detail=i.detail) for i in items],
        hint=_PERMISSION_HINT if _all_forbidden(items) else None,
    )
