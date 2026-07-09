from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class InvitationCreate(BaseModel):
    email: EmailStr


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    email: str
    status: str
    created_at: datetime
    accepted_at: datetime | None


class InvitationCreateResponse(BaseModel):
    invitation: InvitationOut
    invite_link: str


class InvitationPreview(BaseModel):
    org_login: str
    email: str
    status: str


class InvitationAcceptResponse(BaseModel):
    org_login: str
    role: str
