"""Read-only GitHub org roster endpoints: members, outside collaborators, invitations,
membership. Mounted at "/github/orgs/{org_login}/..." directly on the router.

Token resolves via resolve_org_token, falling back to a client PAT carried in the
`X-GitHub-Token` header rather than a query string, which would leak into logs.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Literal

from src.core.db import get_db
from src.core.db import OrgMember as OrgMemberRow
from src.core.rbac import OrgContext, require_org_role
from src.repositories import installation_repo
from src.schemas.collab import (
    CollaboratorPermission,
    InactiveMember,
    InactiveMembersResponse,
    MembershipStatus,
    OrgInvitation,
    OrgInvitationsResponse,
    OrgMember,
    OrgMembersResponse,
    OutsideCollaborator,
    OutsideCollaboratorsResponse,
    PermissionAuditResponse,
    PermissionRiskSummary,
    RepoPermissions,
)
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

# Bounds the per-repo fan-out in list_outside_collaborators -- large orgs are capped
# rather than making hundreds of sequential GitHub calls.
_MAX_REPOS_SCANNED = 50

# permission-audit costs one collaborators call per repo; inactive-members costs one
# commits call per (member, sampled repo) pair -- tighter caps to stay rate-limit-aware.
_MAX_REPOS_FOR_PERMISSION_AUDIT = 20
_MAX_REPOS_SAMPLED_FOR_ACTIVITY = 3


def _resolve_token(db: Session, ctx: OrgContext, org_login: str, client_token: str | None) -> str:
    try:
        return resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=client_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _installation_connected(db: Session, ctx: OrgContext) -> bool:
    """True if this org has a live GitHub App installation -- gates whether list_members
    and inactive_members read from the ingested org_members/repo_events tables instead of
    calling GitHub live.

    Not used to gate list_outside_collaborators or permission_audit: repo_collaborators
    only has rows for grants observed via webhook since connecting (no backfill exists for
    pre-existing direct grants), so those stay live until a repo_collaborators backfill is
    built."""
    installation = installation_repo.get_for_org(db, org_id=ctx.org.id, account_login=ctx.org.github_login)
    return installation is not None and installation.installation_id is not None


def _org_members_synced(db: Session, ctx: OrgContext) -> bool:
    """True only once org_membership_sync_cursors has a row for this tenant -- i.e. at
    least one reconciliation poll has completed, not merely that an installation exists.
    Right after connecting, org_members can still be empty until the first sweep tick
    runs; reading it as authoritative in that window would misleadingly show 0 members.
    A cursor row is only written alongside a successful reconcile, so its presence means
    the table is trustworthy."""
    if not _installation_connected(db, ctx):
        return False
    row = db.execute(
        text("SELECT 1 FROM org_membership_sync_cursors WHERE tenant_id = :tenant_id"), {"tenant_id": ctx.org.tenant_id}
    ).first()
    return row is not None


def _activity_synced(db: Session, ctx: OrgContext) -> bool:
    """Same reasoning as _org_members_synced, but gates inactive_members' repo_events read
    on activity_sync_cursors instead -- same "row exists only after a successful sync"
    invariant."""
    if not _installation_connected(db, ctx):
        return False
    row = db.execute(
        text("SELECT 1 FROM activity_sync_cursors WHERE tenant_id = :tenant_id"), {"tenant_id": ctx.org.tenant_id}
    ).first()
    return row is not None


def _ingested_org_members(db: Session, tenant_id: int, role: Literal["all", "member", "admin"]) -> list[OrgMemberRow]:
    query = db.query(OrgMemberRow).filter(OrgMemberRow.tenant_id == tenant_id)
    if role != "all":
        query = query.filter(OrgMemberRow.role == role)
    return query.order_by(OrgMemberRow.login).all()


@router.get("/github/orgs/{org_login}/members", response_model=OrgMembersResponse)
def list_members(
    org_login: str,
    role: Literal["all", "member", "admin"] = "all",
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    if _org_members_synced(db, ctx):
        rows = _ingested_org_members(db, ctx.org.tenant_id, role)
        members = [
            OrgMember(
                login=r.login,
                avatar_url=r.avatar_url,
                role=r.role,
                # Not captured by ingestion (org_members has no site_admin column); the live
                # path below still reports it accurately for an unconnected org.
                site_admin=False,
                two_factor_enabled=r.two_factor_enabled,
            )
            for r in rows
        ]
        return OrgMembersResponse(
            org=org_login,
            members=members,
            # True only if EVERY member has a real (non-None) 2FA reading -- the UI's "no 2FA"
            # count is gated on this so a partial reading doesn't silently undercount.
            two_factor_overlay_available=bool(members) and all(m.two_factor_enabled is not None for m in members),
        )

    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        admins_raw = client.request_paginated(f"/orgs/{org_login}/members", params={"role": "admin"})
        target_raw = admins_raw if role == "admin" else client.request_paginated(
            f"/orgs/{org_login}/members", params={"role": role}
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    admin_logins = {m["login"] for m in admins_raw}
    members = [
        OrgMember(
            login=m["login"],
            avatar_url=m.get("avatar_url", ""),
            role="admin" if m["login"] in admin_logins else "member",
            site_admin=m.get("site_admin", False),
        )
        for m in target_raw
    ]

    # Best-effort: the 2FA overlay is optional context on the already-succeeded member
    # list, so a failure here degrades to "unavailable" rather than failing the response.
    two_factor_overlay_available = True
    try:
        no_2fa_raw = client.request_paginated(f"/orgs/{org_login}/members", params={"filter": "2fa_disabled"})
        no_2fa_logins = {m["login"] for m in no_2fa_raw}
        for member in members:
            member.two_factor_enabled = member.login not in no_2fa_logins
    except (httpx.HTTPStatusError, httpx.RequestError):
        two_factor_overlay_available = False

    return OrgMembersResponse(org=org_login, members=members, two_factor_overlay_available=two_factor_overlay_available)


@router.get("/github/orgs/{org_login}/outside_collaborators", response_model=OutsideCollaboratorsResponse)
def list_outside_collaborators(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        outside_raw = client.request_paginated(f"/orgs/{org_login}/outside_collaborators")
        # Fetches the full repo list (not just the first _MAX_REPOS_SCANNED) so
        # repos_total is exact -- the UI's "scanned X of Y" note depends on it.
        repos_raw = client.request_paginated(f"/orgs/{org_login}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    repos_total = len(repos_raw)
    scanned_repos = repos_raw[:_MAX_REPOS_SCANNED]
    repos_scanned = len(scanned_repos)

    repos_by_login: dict[str, list[str]] = {c["login"]: [] for c in outside_raw}
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(
                    client.request_paginated,
                    f"/repos/{org_login}/{repo['name']}/collaborators",
                    params={"affiliation": "outside"},
                ): repo["name"]
                for repo in scanned_repos
            }
            for future, repo_name in futures.items():
                for collab in future.result():
                    if collab["login"] in repos_by_login:
                        repos_by_login[collab["login"]].append(f"{org_login}/{repo_name}")
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    collaborators = [
        OutsideCollaborator(
            login=c["login"],
            avatar_url=c.get("avatar_url", ""),
            repos=repos_by_login.get(c["login"], []),
        )
        for c in outside_raw
    ]

    return OutsideCollaboratorsResponse(
        org=org_login, collaborators=collaborators, repos_scanned=repos_scanned, repos_total=repos_total
    )


@router.get("/github/orgs/{org_login}/invitations", response_model=OrgInvitationsResponse)
def list_github_invitations(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        raw = client.request_paginated(f"/orgs/{org_login}/invitations")
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    invitations = [
        OrgInvitation(
            login=i.get("login"),
            email=i.get("email"),
            role=i.get("role", ""),
            invited_at=i["created_at"],
            inviter=(i.get("inviter") or {}).get("login"),
        )
        for i in raw
        # Skip rather than 500 the whole list if a malformed entry is missing created_at.
        if "created_at" in i
    ]
    return OrgInvitationsResponse(org=org_login, invitations=invitations)


@router.get("/github/orgs/{org_login}/members/{username}/membership", response_model=MembershipStatus)
def get_membership(
    org_login: str,
    username: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        raw = client.request("GET", f"/orgs/{org_login}/members/{username}/membership")
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    return MembershipStatus(state=raw["state"], role=raw["role"])


_PERMISSION_RANK = ["pull", "triage", "push", "maintain", "admin"]
_PERMISSION_DISPLAY = {"pull": "read", "triage": "triage", "push": "write", "maintain": "maintain", "admin": "admin"}


def _collaborator_permission(raw: dict) -> str:
    perms = raw.get("permissions") or {}
    for key in reversed(_PERMISSION_RANK):
        if perms.get(key):
            return _PERMISSION_DISPLAY[key]
    return "read"


@router.get("/github/orgs/{org_login}/permission-audit", response_model=PermissionAuditResponse)
def permission_audit(
    org_login: str,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        member_logins = {m["login"] for m in client.request_paginated(f"/orgs/{org_login}/members") if "login" in m}
        outside_logins = {
            c["login"] for c in client.request_paginated(f"/orgs/{org_login}/outside_collaborators") if "login" in c
        }
        repos_raw = client.request_paginated(f"/orgs/{org_login}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    repos_total = len(repos_raw)
    scanned_repos = repos_raw[:_MAX_REPOS_FOR_PERMISSION_AUDIT]

    def _fetch_repo_collaborators(repo_name: str) -> list[dict]:
        try:
            return client.request_paginated(f"/repos/{org_login}/{repo_name}/collaborators")
        except (httpx.HTTPStatusError, httpx.RequestError):
            return []

    with ThreadPoolExecutor(max_workers=10) as pool:
        raw_by_repo = dict(
            zip(
                (r["name"] for r in scanned_repos),
                pool.map(lambda r: _fetch_repo_collaborators(r["name"]), scanned_repos),
            )
        )

    repos: list[RepoPermissions] = []
    outside_with_elevated: set[str] = set()
    members_with_admin: set[str] = set()
    all_outside_seen: set[str] = set()

    for repo_name, raw_collabs in raw_by_repo.items():
        collaborators = []
        for c in raw_collabs:
            # A malformed collaborator entry missing `login` shouldn't 500 the whole row.
            if "login" not in c:
                continue
            login = c["login"]
            is_outside = login not in member_logins or login in outside_logins
            permission = _collaborator_permission(c)
            collaborators.append(
                CollaboratorPermission(
                    login=login,
                    avatar_url=c.get("avatar_url", ""),
                    permission=permission,
                    affiliation="outside" if is_outside else "direct",
                    is_outside_collaborator=is_outside,
                )
            )
            if is_outside:
                all_outside_seen.add(login)
                if permission in ("write", "maintain", "admin"):
                    outside_with_elevated.add(login)
            elif permission == "admin":
                members_with_admin.add(login)
        repos.append(RepoPermissions(repo=repo_name, collaborators=collaborators))

    return PermissionAuditResponse(
        generated_at=datetime.now(timezone.utc),
        repos_scanned=len(scanned_repos),
        repos_total=repos_total,
        repos=repos,
        risk_summary=PermissionRiskSummary(
            outside_with_write_or_admin=len(outside_with_elevated),
            members_with_admin=len(members_with_admin),
            total_outside_collaborators=len(all_outside_seen),
        ),
    )


def _last_commit_for_author(client: GitHubClient, org_login: str, repo_name: str, login: str) -> tuple[str | None, bool]:
    """Returns (last_commit_date, checked). `checked=False` means the GitHub call itself
    failed -- an unknown answer, not evidence of zero commits, and must not be conflated
    with a real "checked, found nothing" result."""
    try:
        commits = client.request(
            "GET", f"/repos/{org_login}/{repo_name}/commits", params={"author": login, "per_page": 1}
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None, False
    if not isinstance(commits, list) or not commits:
        return None, True
    date = ((commits[0].get("commit") or {}).get("author") or {}).get("date")
    return date, True


def _inactive_members_from_ingested(db: Session, ctx: OrgContext, org_login: str, days: int) -> InactiveMembersResponse:
    """Uses ingested tables for a connected org: org_members for the roster, repo_events for
    last-push-per-member. Strictly more accurate than the live path below, which only samples
    a few repos per member -- this checks every repo with an ingested push event in one query."""
    members = _ingested_org_members(db, ctx.org.tenant_id, "all")
    logins = [m.login for m in members]
    now = datetime.now(timezone.utc)

    last_push_by_login: dict[str, tuple[str, datetime]] = {}
    if logins:
        rows = db.execute(
            text(
                "SELECT DISTINCT ON (actor) actor, repo, occurred_at FROM repo_events "
                "WHERE tenant_id = :tenant_id AND event_type = 'push' AND actor = ANY(:logins) "
                "ORDER BY actor, occurred_at DESC"
            ),
            {"tenant_id": ctx.org.tenant_id, "logins": logins},
        ).fetchall()
        last_push_by_login = {row.actor: (row.repo, row.occurred_at) for row in rows}

    inactive: list[InactiveMember] = []
    for member in members:
        last = last_push_by_login.get(member.login)
        repo_name, days_ago = None, None
        if last is not None:
            repo_name, occurred_at = last
            days_ago = (now - occurred_at).days
        if days_ago is None or days_ago >= days:
            inactive.append(
                InactiveMember(
                    login=member.login,
                    avatar_url=member.avatar_url,
                    role=member.role,
                    last_commit_repo=repo_name,
                    last_commit_days_ago=days_ago,
                )
            )

    return InactiveMembersResponse(
        org=org_login,
        # Not a bounded sample here (unlike the live path) -- every repo with an ingested
        # push event was considered; this lists which ones contributed a member's activity.
        sampled_repos=sorted({repo for repo, _ in last_push_by_login.values()}),
        members=inactive,
    )


@router.get("/github/orgs/{org_login}/inactive-members", response_model=InactiveMembersResponse)
def inactive_members(
    org_login: str,
    days: int = 30,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    # Needs both cursors: roster from org_members, last-activity from repo_events -- either
    # one not yet synced means this endpoint can't trust the ingested tables yet.
    if _org_members_synced(db, ctx) and _activity_synced(db, ctx):
        return _inactive_members_from_ingested(db, ctx, org_login, days)

    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        admins_raw = client.request_paginated(f"/orgs/{org_login}/members", params={"role": "admin"})
        members_raw = client.request_paginated(f"/orgs/{org_login}/members")
        repos_raw = client.request_paginated(f"/orgs/{org_login}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    admin_logins = {m["login"] for m in admins_raw if "login" in m}
    sampled_repos = [r["name"] for r in repos_raw[:_MAX_REPOS_SAMPLED_FOR_ACTIVITY]]
    now = datetime.now(timezone.utc)

    def _member_last_activity(member: dict) -> tuple[str | None, str | None, bool]:
        login = member.get("login")
        if not login:
            return None, None, False
        verified = False
        for repo_name in sampled_repos:
            date, checked = _last_commit_for_author(client, org_login, repo_name, login)
            if checked:
                verified = True
            if date:
                return repo_name, date, True
        # verified=True means every sampled repo was queried and genuinely had no commits;
        # verified=False means a lookup failed, so activity is unknown, not confirmed absent.
        return None, None, verified

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_member_last_activity, members_raw))

    inactive: list[InactiveMember] = []
    for member, (repo_name, date, verified) in zip(members_raw, results):
        login = member.get("login")
        if not login or not verified:
            # Can't confirm this member's activity -- don't claim inactivity we can't back up.
            continue
        days_ago = None
        if date:
            try:
                last = datetime.fromisoformat(date.replace("Z", "+00:00"))
                days_ago = (now - last).days
            except ValueError:
                days_ago = None
        if days_ago is None or days_ago >= days:
            inactive.append(
                InactiveMember(
                    login=login,
                    avatar_url=member.get("avatar_url", ""),
                    role="admin" if login in admin_logins else "member",
                    last_commit_repo=f"{org_login}/{repo_name}" if repo_name else None,
                    last_commit_days_ago=days_ago,
                )
            )

    return InactiveMembersResponse(org=org_login, sampled_repos=[f"{org_login}/{r}" for r in sampled_repos], members=inactive)
