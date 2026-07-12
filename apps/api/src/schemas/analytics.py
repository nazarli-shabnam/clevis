from pydantic import BaseModel, SecretStr


class AnalyticsInput(BaseModel):
    owner: str
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this owner (see src.services.token_resolution).
    token: SecretStr | None = None


class AnalyticsResponse(BaseModel):
    owner: str
    score: int
    total_checks: int
    failed_checks: int
    repo_count: int
    checks: list[dict]
