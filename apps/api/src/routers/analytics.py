import logging

import anyio
import httpx
from fastapi import APIRouter, HTTPException

from src.schemas.analytics import AnalyticsInput, AnalyticsResponse
from src.services.analytics_service import get_overview

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/overview", response_model=AnalyticsResponse)
async def analytics_overview(payload: AnalyticsInput):
    try:
        result = await anyio.to_thread.run_sync(
            lambda: get_overview(owner=payload.owner, token=payload.token.get_secret_value())
        )
        return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")
    except Exception:
        logger.exception("analytics_overview failed")
        raise HTTPException(status_code=500, detail="Internal error")
