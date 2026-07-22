from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class OrgMember(BaseModel):
    login: str
    avatar_url: str
    role: Literal["member", "admin"]
    site_admin: bool
    # None means the 2FA overlay wasn't available (token lacks org-owner scope),
    # not that 2FA status is unknown-but-checkable.
    two_factor_enabled: bool | None = None


class OrgMembersResponse(BaseModel):
    org: str
    members: list[OrgMember]
    two_factor_overlay_available: bool


class OutsideCollaborator(BaseModel):
    login: str
    avatar_url: str
    repos: list[str]


class OutsideCollaboratorsResponse(BaseModel):
    org: str
    collaborators: list[OutsideCollaborator]
    repos_scanned: int
    repos_total: int


class OrgInvitation(BaseModel):
    login: str | None
    email: str | None
    role: str
    invited_at: datetime
    inviter: str | None


class OrgInvitationsResponse(BaseModel):
    org: str
    invitations: list[OrgInvitation]


class MembershipStatus(BaseModel):
    state: Literal["active", "pending"]
    role: Literal["member", "admin"]
