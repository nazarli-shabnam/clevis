import logging

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import installation_repo, org_membership_repo, org_repo, scan_results_repo
from src.schemas.analytics import AnalyticsInput, AnalyticsResponse, ScanHistoryEntry
from src.services.analytics_service import get_account_type, get_overview
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token, resolve_personal_token

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_overview(owner: str, token: str) -> AnalyticsResponse:
    try:
        return await anyio.to_thread.run_sync(lambda: get_overview(owner=owner, token=token))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")
    except Exception:
        logger.exception("analytics_overview failed")
        raise HTTPException(status_code=500, detail="Internal error")


async def _get_account_type(owner: str, token: str) -> str:
    try:
        return await anyio.to_thread.run_sync(lambda: get_account_type(owner=owner, token=token))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")


def _persist_scan(db: Session, result: dict, scanned_by_user_id: int | None = None) -> None:
    scan_results_repo.insert(
        db,
        owner=result["owner"],
        score=result["score"],
        total_checks=result["total_checks"],
        failed_checks=result["failed_checks"],
        checks=result["checks"],
        scanned_by_user_id=scanned_by_user_id,
    )


def _user_can_read_history(db: Session, user: UserOut, owner: str) -> bool:
    """Scan history isn't gated by GitHub-side authorization the way a live scan is
    (GitHub itself rejects a bad token/owner combo) -- it's a local DB read, so it
    needs its own access check. A user may read `owner`'s history if they're a
    member of the matching workspace Org, they personally have a GitHub App
    installation connected for that account login, or they're the one who ran a
    personal scan against that owner before (scanned_by_user_id, for owners with
    no workspace Org/membership at all -- the raw-PAT-paste flow)."""
    org = org_repo.get_by_login(db, owner)
    if org is not None and org_membership_repo.get(db, org.id, user.id) is not None:
        return True
    if installation_repo.get_for_user(db, owner_user_id=user.id, account_login=owner) is not None:
        return True
    return scan_results_repo.exists_for_user(db, owner=owner, user_id=user.id)


@router.post("/orgs/{org_login}/analytics/overview", response_model=AnalyticsResponse)
async def org_analytics_overview(
    payload: AnalyticsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    assert_owner_matches_org(payload.owner, ctx)
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_org_token(db, org_id=ctx.org.id, account_login=payload.owner, client_token=client_token)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = await _run_overview(payload.owner, token)
    _persist_scan(db, result)
    return result


@router.post("/me/analytics/overview", response_model=AnalyticsResponse)
async def personal_analytics_overview(
    payload: AnalyticsInput,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    client_token = payload.token.get_secret_value() if payload.token else None
    try:
        token = await anyio.to_thread.run_sync(
            lambda: resolve_personal_token(
                db, owner_user_id=user.id, account_login=payload.owner, client_token=client_token
            )
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    account_type = await _get_account_type(payload.owner, token)
    if account_type == "User":
        raise HTTPException(
            status_code=422,
            detail="Personal GitHub accounts aren't supported for security scanning yet. Connect a GitHub organization instead.",
        )
    result = await _run_overview(payload.owner, token)
    _persist_scan(db, result, scanned_by_user_id=user.id)
    return result


@router.get("/orgs/{org_login}/analytics/history", response_model=list[ScanHistoryEntry])
def org_analytics_history(
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    return scan_results_repo.list_recent(db, owner=ctx.org.github_login, limit=30)


@router.get("/me/analytics/history", response_model=list[ScanHistoryEntry])
def personal_analytics_history(
    owner: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if not _user_can_read_history(db, user, owner):
        raise HTTPException(status_code=403, detail="You don't have access to this owner's scan history")
    return scan_results_repo.list_recent(db, owner=owner, limit=30)
