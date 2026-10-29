from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, model_validator


class DestinationCreate(BaseModel):
    kind: Literal["slack", "teams", "generic", "email"]
    name: str = Field(min_length=1, max_length=100)
    # The webhook URL for slack/teams/generic; not used by `email`.
    url: SecretStr | None = None
    # `email` only: where to send. Must be verified members of the org (checked by the route).
    recipients: list[EmailStr] | None = Field(default=None, max_length=10)
    # HMAC-SHA256 signing secret; only meaningful for `generic`.
    secret: SecretStr | None = None
    events: list[Literal["score_drop", "waiver_expired"]] = ["score_drop"]
    min_score_drop: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def _shape_matches_kind(self):
        if self.kind == "email":
            if not self.recipients:
                raise ValueError("an email destination needs at least one recipient")
            if self.url is not None:
                raise ValueError("an email destination takes recipients, not a url")
        elif self.url is None:
            raise ValueError("url is required")
        return self


class DestinationOut(BaseModel):
    """Never includes the URL or secret: a webhook URL is itself a credential."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    name: str
    events: list[str]
    min_score_drop: int
    enabled: bool
    created_at: datetime
    signed: bool = False
    # `email` destinations only (an admin-only endpoint): who receives it.
    recipients: list[str] | None = None


class TestSendResult(BaseModel):
    ok: bool
    detail: str
