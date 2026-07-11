from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr

InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]


class InvitationCreate(BaseModel):
    email: EmailStr


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


class InvitationPreview(BaseModel):
    # Deliberately excludes the invitee's email — this endpoint is unauthenticated
    # and invite links are shareable, so the email shouldn't be disclosed pre-auth.
    org_login: str
    status: InvitationStatus


class InvitationAcceptResponse(BaseModel):
    org_login: str
    role: str
