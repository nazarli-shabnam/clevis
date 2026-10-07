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
    # Set only by the live-GitHub fallback, which checks at most a bounded number of members:
    # members_checked counts members whose activity was actually verified; below members_total means
    # the roster was longer than the cap or some lookups failed, so the list is incomplete.
    members_total: int | None = None
    members_checked: int | None = None


class MemberRepoGrant(BaseModel):
    repo: str
    permission: str
    # None = not yet known (a `member` webhook alone cannot tell org members from outside collaborators).
    is_outside_collaborator: bool | None = None
    granted_at: datetime


class MemberAccess(BaseModel):
    """What Clevis has ingested about one person's access, for offboarding review.

    Built only from webhook- and poll-ingested tables, so it costs no GitHub calls. The grants are
    DIRECT repo grants seen since the App was connected: access through teams or the org's base
    permission is not in the ingested data, so an empty list is not proof of no access."""

    org: str
    login: str
    # False until the org's first membership sync, i.e. nothing below is trustworthy yet.
    synced: bool
    # None when not synced: "unknown" must not read as "not a member".
    is_member: bool | None = None
    # False until the org's first activity backfill: the last_* fields are then null because repo_events
    # is empty or partial, not because the person is dormant.
    activity_synced: bool = False
    role: Literal["member", "admin"] | None = None
    two_factor_enabled: bool | None = None
    last_event_at: datetime | None = None
    last_push_at: datetime | None = None
    last_push_repo: str | None = None
    direct_grants: list[MemberRepoGrant]
