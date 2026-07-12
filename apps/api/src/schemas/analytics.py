from pydantic import BaseModel, SecretStr


class AnalyticsInput(BaseModel):
    owner: str
    token: SecretStr


class AnalyticsResponse(BaseModel):
    owner: str
    score: int
    total_checks: int
    failed_checks: int
    repo_count: int
    checks: list[dict]
