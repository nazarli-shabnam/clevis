import logging

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.schemas.analytics import AnalyticsInput, AnalyticsResponse
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
    return await _run_overview(payload.owner, token)


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
    return await _run_overview(payload.owner, token)
