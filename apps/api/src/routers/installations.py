"""GitHub App installation router.

  GET  /orgs/{org_login}/installations       member: list installations connected to this org
  POST /orgs/{org_login}/installations/sync  admin: re-sync installation metadata for an
                                              *existing* org. A brand-new org's first
                                              installation is only created via the
                                              auto-provisioning flow (src.services.org_provisioning,
                                              run at OAuth login) — this endpoint can't bootstrap a
                                              brand-new Org row since it has no way to verify the
                                              caller is a real GitHub org admin.
  GET  /me/installations                     list the current user's personal installations
  POST /me/installations/sync                connect a personal (User-type) GitHub installation
  GET  /me/installations/lookup/{id}          resolve an installation_id to the account it belongs
                                              to, so the post-install UI callback (which only gets
                                              installation_id/setup_action from GitHub) knows
                                              whether to call the /me or /orgs sync endpoint next.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import installation_repo
from src.schemas.installation import (
    InstallationLookupOut,
    InstallationOut,
    SyncInstallationsInput,
    SyncInstallationsResponse,
)
from src.services import github_app

router = APIRouter()


def _fetch_installation(installation_id: int) -> dict:
    """Look up an installation_id via the GitHub App's own credentials, mapping
    GitHub/transport errors to the HTTPException shape callers expect."""
    try:
        return github_app.get_installation(installation_id)
    except github_app.GitHubAppNotConfigured:
        raise HTTPException(
            status_code=503,
            detail="GitHub App is not configured; cannot verify installation_id",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=422, detail=f"installation_id {installation_id} does not exist")
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")


def _verify_installation(installation_id: int | None, account_login: str, account_type: str) -> None:
    """Confirm a client-supplied installation_id genuinely belongs to the claimed
    account before it's persisted as trusted data. No-op if installation_id wasn't
    supplied — there's nothing to verify in that case."""
    if installation_id is None:
        return
    installation = _fetch_installation(installation_id)

    account = installation.get("account") or {}
    actual_login = account.get("login", "")
    actual_type = account.get("type", "")
    if actual_login.lower() != account_login.lower() or actual_type != account_type:
        raise HTTPException(
            status_code=422,
            detail=(
                f"installation_id {installation_id} belongs to {actual_type} "
                f"'{actual_login}', not {account_type} '{account_login}'"
            ),
        )


@router.get("/me/installations/lookup/{installation_id}", response_model=InstallationLookupOut)
def lookup_installation(
    installation_id: int,
    _user: UserOut = Depends(require_auth),
):
    installation = _fetch_installation(installation_id)
    account = installation.get("account") or {}
    return {
        "account_login": account.get("login", ""),
        "account_type": account.get("type", ""),
    }


@router.get("/orgs/{org_login}/installations", response_model=list[InstallationOut])
def list_org_installations(
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    return installation_repo.list_for_org(db, org_id=ctx.org.id)


@router.post("/orgs/{org_login}/installations/sync", response_model=SyncInstallationsResponse)
def sync_org_installation(
    payload: SyncInstallationsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(payload.account_login, ctx)
    _verify_installation(payload.installation_id, payload.account_login, payload.account_type)
    row = installation_repo.create(
        db,
        account_login=payload.account_login,
        account_type=payload.account_type,
        auth_mode=payload.auth_mode,
        installation_id=payload.installation_id,
        org_id=ctx.org.id,
    )
    return {"synced": True, "token_ref": row.token_ref}


@router.get("/me/installations", response_model=list[InstallationOut])
def list_personal_installations(
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    return installation_repo.list_for_user(db, owner_user_id=user.id)


@router.post("/me/installations/sync", response_model=SyncInstallationsResponse)
def sync_personal_installation(
    payload: SyncInstallationsInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if payload.account_type != "User":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Personal installation sync only supports account_type User; use org sync for organizations",
        )
    db_user = db.query(User).filter(User.id == user.id).first()
    if not db_user or not db_user.github_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Link your GitHub account before syncing a personal installation",
        )
    if payload.account_login != db_user.github_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="account_login must match your own GitHub account",
        )
    _verify_installation(payload.installation_id, payload.account_login, payload.account_type)
    row = installation_repo.create(
        db,
        account_login=payload.account_login,
        account_type=payload.account_type,
        auth_mode=payload.auth_mode,
        installation_id=payload.installation_id,
        owner_user_id=user.id,
    )
    return {"synced": True, "token_ref": row.token_ref}
