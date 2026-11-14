from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, StringConstraints

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


EmailStatus = Literal["sent", "failed"]


class InvitationCreate(BaseModel):
    email: EmailStr
    # Opt in to emailing the link too (needs SMTP). Off by default: the link is always returned to copy.
    send_email: bool = False


MAX_BULK_INVITES = 50
# RFC 5321 caps an address at 254 characters, so a longer string can't be one. Rejecting it up front
# (the whole request 422s) keeps each rejected string we echo back in the response bounded.
MAX_EMAIL_LENGTH = 254


class InvitationBulkCreate(BaseModel):
    # Plain strings, not EmailStr: one malformed address must come back as an "invalid" result for
    # that address instead of failing the whole request with a 422.
    # Stripped before the length check, so padding around a valid address can't 422 the request.
    emails: list[
        Annotated[
            str,
            BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
            StringConstraints(max_length=MAX_EMAIL_LENGTH),
        ]
    ] = Field(
        min_length=1, max_length=MAX_BULK_INVITES
    )
    send_email: bool = False


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
    # None unless `send_email` was requested. "failed" still leaves a valid invitation and link.
    email_status: EmailStatus | None = None


class InvitationBulkItem(BaseModel):
    email: str
    status: Literal["created", "already_pending", "invalid", "failed"]
    invitation: InvitationOut | None = None
    invite_link: str | None = None
    email_status: EmailStatus | None = None


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
