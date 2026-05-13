from pydantic import BaseModel


class AnalyticsInput(BaseModel):
    owner: str
    token: str


class AnalyticsResponse(BaseModel):
    owner: str
    score: int
    total_checks: int
    failed_checks: int
    checks: list[dict]
