import httpx
from fastapi import APIRouter, HTTPException

from src.schemas.analytics import AnalyticsInput, AnalyticsResponse
from src.services.analytics_service import get_overview

router = APIRouter()


@router.post("/overview", response_model=AnalyticsResponse)
def analytics_overview(payload: AnalyticsInput):
    try:
        return get_overview(owner=payload.owner, token=payload.token.get_secret_value())
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=400, detail=f"GitHub API error: {exc.response.status_code}")
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="GitHub API unreachable")
    except Exception:
        raise HTTPException(status_code=500, detail="Internal error")
