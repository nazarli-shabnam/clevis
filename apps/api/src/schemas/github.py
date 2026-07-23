from datetime import datetime

from pydantic import BaseModel, SecretStr


class OrgEventsInput(BaseModel):
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this org (see src.services.token_resolution).
    token: SecretStr | None = None
    per_page: int = 30


class OrgEvent(BaseModel):
    id: str
    type: str
    actor: str
    actor_avatar: str
    repo: str
    summary: str
    created_at: datetime


class OrgEventsResponse(BaseModel):
    org: str
    events: list[OrgEvent]


class FailedRunsInput(BaseModel):
    token: SecretStr | None = None
    limit: int = 20


class FailedRunSummary(BaseModel):
    repo: str
    workflow_name: str
    branch: str
    run_id: int
    started_at: datetime
    duration_seconds: int | None
    url: str
    actor: str
    consecutive_failures: int


class FailedRunsResponse(BaseModel):
    org: str
    runs: list[FailedRunSummary]


class ReleaseTimelineInput(BaseModel):
    token: SecretStr | None = None
    days: int = 90


class ReleaseSummary(BaseModel):
    repo: str
    tag_name: str
    name: str
    published_at: datetime
    is_prerelease: bool
    body_preview: str
    url: str


class ReleaseTimelineResponse(BaseModel):
    org: str
    releases: list[ReleaseSummary]
