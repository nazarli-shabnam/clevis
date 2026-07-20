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
