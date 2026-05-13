from fastapi import APIRouter, HTTPException

from src.schemas.analytics import AnalyticsInput, AnalyticsResponse
from src.services.analytics_service import get_overview

router = APIRouter()


@router.post("/overview", response_model=AnalyticsResponse)
def analytics_overview(payload: AnalyticsInput):
    try:
        return get_overview(owner=payload.owner, token=payload.token)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))
