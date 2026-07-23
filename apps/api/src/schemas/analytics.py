from datetime import datetime

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


class ScanHistoryEntry(BaseModel):
    id: int
    owner: str
    score: int
    total_checks: int
    failed_checks: int
    created_at: datetime


class OrgEventSummary(BaseModel):
    id: str
    type: str
    actor: str
    actor_avatar: str
    repo: str
    summary: str
    created_at: datetime


class PrWeekBucket(BaseModel):
    week: str
    opened: int
    merged: int


class CockpitResponse(BaseModel):
    repo_count: int
    member_count: int
    latest_score: int | None
    score_trend: list[int]
    recent_events: list[OrgEventSummary]
    open_pr_count: int
    pr_merge_rate_4w: list[PrWeekBucket]
    commit_activity_4w: list[int]
    total_cache_size_bytes: int
    cache_job_success_rate: float
    commit_heatmap_52w: list[int] = []
