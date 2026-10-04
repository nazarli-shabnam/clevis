from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiTokenOut(BaseModel):
    """Never includes the token itself, only its short prefix."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    scope: str
    created_by: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiTokenCreated(ApiTokenOut):
    # Shown exactly once, at creation.
    token: str


class BadgeSettings(BaseModel):
    enabled: bool


class ScoreCheck(BaseModel):
    id: str
    title: str
    status: str


class ScoreOut(BaseModel):
    """Machine-readable score: no per-check detail values, only id/title/status."""

    org: str
    score: int
    total_checks: int
    failed_checks: int
    scanned_at: str | None = None
    checks: list[ScoreCheck]
