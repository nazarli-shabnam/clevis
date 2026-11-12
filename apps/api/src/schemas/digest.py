from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Cadence = Literal["weekly", "monthly"]


class DigestSettings(BaseModel):
    enabled: bool | None  # the org's override; None = follows the instance
    effective: bool  # whether this org gets a digest at all
    cadence: Cadence | None
    instance_cadence: Literal["off", "weekly", "monthly"]
    # The admin's chosen recipients; None = every verified org admin.
    recipients: list[str] | None
    # Who would receive it right now (chosen or default, verified members only).
    resolved_recipients: list[str]
    # The addresses that may be chosen: the org's verified members.
    available_recipients: list[str]
    smtp_configured: bool


class DigestSettingsUpdate(BaseModel):
    """Only the fields sent change. ``enabled: null`` follows the instance again; ``recipients: null``
    goes back to every verified org admin."""

    enabled: bool | None = None
    recipients: list[EmailStr] | None = Field(default=None, min_length=1, max_length=25)


class DigestPreview(BaseModel):
    subject: str
    body: str
    # No scan has run yet: the digest would not be sent.
    empty: bool


class DigestTestResult(BaseModel):
    sent_to: str
