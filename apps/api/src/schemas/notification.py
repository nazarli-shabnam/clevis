from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class DestinationCreate(BaseModel):
    kind: Literal["slack", "teams", "generic"]
    name: str = Field(min_length=1, max_length=100)
    url: SecretStr
    # HMAC-SHA256 signing secret; only meaningful for `generic`.
    secret: SecretStr | None = None
    events: list[Literal["score_drop"]] = ["score_drop"]
    min_score_drop: int = Field(default=10, ge=1, le=100)


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


class TestSendResult(BaseModel):
    ok: bool
    detail: str
