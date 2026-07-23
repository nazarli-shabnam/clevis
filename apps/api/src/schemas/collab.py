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


class CollaboratorPermission(BaseModel):
    login: str
    avatar_url: str
    permission: Literal["read", "triage", "write", "maintain", "admin"]
    affiliation: Literal["direct", "outside"]
    is_outside_collaborator: bool


class RepoPermissions(BaseModel):
    repo: str
    collaborators: list[CollaboratorPermission]


class PermissionRiskSummary(BaseModel):
    outside_with_write_or_admin: int
    members_with_admin: int
    total_outside_collaborators: int


class PermissionAuditResponse(BaseModel):
    generated_at: datetime
    repos_scanned: int
    repos_total: int
    repos: list[RepoPermissions]
    risk_summary: PermissionRiskSummary


class InactiveMember(BaseModel):
    login: str
    avatar_url: str
    role: Literal["member", "admin"]
    last_commit_repo: str | None
    last_commit_days_ago: int | None


class InactiveMembersResponse(BaseModel):
    org: str
    # Honest-approximation note surfaced to the UI: GitHub has no "last activity"
    # API, so this samples commit authorship across a bounded set of repos rather
    # than being an exact answer.
    sampled_repos: list[str]
    members: list[InactiveMember]
