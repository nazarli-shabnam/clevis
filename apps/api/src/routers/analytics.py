import logging

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException

from src.core.auth import UserOut, require_auth
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.schemas.analytics import AnalyticsInput, AnalyticsResponse
from src.services.analytics_service import get_overview

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_overview(payload: AnalyticsInput) -> AnalyticsResponse:
    try:
        return await anyio.to_thread.run_sync(
            lambda: get_overview(owner=payload.owner, token=payload.token.get_secret_value())
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")
    except Exception:
        logger.exception("analytics_overview failed")
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/orgs/{org_login}/analytics/overview", response_model=AnalyticsResponse)
async def org_analytics_overview(
    payload: AnalyticsInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
):
    assert_owner_matches_org(payload.owner, ctx)
    return await _run_overview(payload)


@router.post("/me/analytics/overview", response_model=AnalyticsResponse)
async def personal_analytics_overview(payload: AnalyticsInput, _user: UserOut = Depends(require_auth)):
    return await _run_overview(payload)
