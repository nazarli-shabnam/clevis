"""File a GitHub issue from a Clevis finding.

Personal-scoped (``/me/...``); token resolution goes through ``resolve_owner_token``
with ``min_role="admin"`` since this writes to GitHub. Requires ``Issues: write`` on the
installation or PAT -- a 403 surfaces as a 400 with a "needs Issues: write" hint.
"""

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import audit_tenant
from src.repositories import audit_repo
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import (
    InsufficientOrgRole,
    NoGitHubTokenAvailable,
    resolve_owner_token,
)

router = APIRouter()


class CreateIssueRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=65536)
    token: str | None = None


class CreateIssueResponse(BaseModel):
    number: int
    html_url: str


@router.post("/me/repos/{owner}/{repo}/issues", response_model=CreateIssueResponse, status_code=201)
def create_issue(
    owner: str,
    repo: str,
    body: CreateIssueRequest,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
) -> CreateIssueResponse:
    """Create a GitHub issue in ``{owner}/{repo}``. If ``owner`` is a connected Clevis org
    the caller must be an **admin** of it; the resolved token (installation or PAT) must
    carry ``Issues: write``."""
    try:
        token = resolve_owner_token(
            db,
            user_id=user.id,
            owner=owner,
            client_token=body.token or x_github_token,
            min_role="admin",
        )
    except InsufficientOrgRole as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tenant_id = audit_tenant(db, user.id, owner)
    # Audit the attempt before it reaches GitHub, so a rejected write is still recorded.
    audit_repo.write(
        db, user.email, "issues.create", f"{owner}/{repo}", {"title": body.title}, tenant_id=tenant_id
    )

    try:
        created = GitHubClient(token).request(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json={"title": body.title, "body": body.body},
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    return CreateIssueResponse(number=created["number"], html_url=created["html_url"])
