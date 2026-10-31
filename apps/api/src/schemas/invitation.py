from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class InvitationCreate(BaseModel):
    email: EmailStr


MAX_BULK_INVITES = 50
# RFC 5321 caps an address at 254 characters, so a longer string can't be one. Rejecting it up front
# (the whole request 422s) keeps each rejected string we echo back in the response bounded.
MAX_EMAIL_LENGTH = 254


class InvitationBulkCreate(BaseModel):
    # Plain strings, not EmailStr: one malformed address must come back as an "invalid" result for
    # that address instead of failing the whole request with a 422.
    emails: list[Annotated[str, StringConstraints(max_length=MAX_EMAIL_LENGTH)]] = Field(
        min_length=1, max_length=MAX_BULK_INVITES
    )


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    email: str
    status: InvitationStatus
    created_at: datetime
    accepted_at: datetime | None
    expires_at: datetime


class InvitationCreateResponse(BaseModel):
    invitation: InvitationOut
    invite_link: str


class InvitationBulkItem(BaseModel):
    email: str
    status: Literal["created", "already_pending", "invalid", "failed"]
    invitation: InvitationOut | None = None
    invite_link: str | None = None


class InvitationBulkResponse(BaseModel):
    results: list[InvitationBulkItem]


class InvitationPreview(BaseModel):
    # Deliberately excludes the invitee's email — this endpoint is unauthenticated
    # and invite links are shareable, so the email shouldn't be disclosed pre-auth.
    org_login: str
    status: InvitationStatus


class InvitationAcceptResponse(BaseModel):
    org_login: str
    role: str
