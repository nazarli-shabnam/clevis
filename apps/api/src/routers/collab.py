"""Read-only GitHub org roster endpoints (docs/plan.md Phase 11 — Collaborators).

Proxies GitHub's org members / outside-collaborators / invitations / membership
endpoints. No mutation routes here — invite/revoke actions are a later phase.

Mounted with the full "/github/orgs/{org_login}/..." path on the router itself
(no `prefix=` passed to include_router in main.py), matching github.py's
convention for GitHub-proxy routers. Resolves a token via resolve_org_token,
preferring a GitHub App installation but falling back to a client-supplied PAT
carried in the `X-GitHub-Token` request header -- these are plain GETs, so a
PAT can't travel in the body the way every other GitHub-proxy POST route does;
a header (never a query string, which would leak into logs/browser history) is
the safe equivalent, matching the "PAT is optional if the App is connected"
pattern used everywhere else in this codebase.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from typing import Literal

from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
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

# Bounds the per-repo fan-out in list_outside_collaborators -- each additional
# repo costs one more GitHub call, so large orgs are capped rather than left to
# make hundreds of sequential requests.
_MAX_REPOS_SCANNED = 50

# permission-audit costs one collaborators call per repo; inactive-members costs
# one commits call per (member, sampled repo) pair -- both tighter caps than the
# single-call-per-repo endpoints above, rate-limit-aware per docs/plan.md Phase 18.
_MAX_REPOS_FOR_PERMISSION_AUDIT = 20
_MAX_REPOS_SAMPLED_FOR_ACTIVITY = 3


def _resolve_token(db: Session, ctx: OrgContext, org_login: str, client_token: str | None) -> str:
    try:
        return resolve_org_token(db, org_id=ctx.org.id, account_login=org_login, client_token=client_token)
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/github/orgs/{org_login}/members", response_model=OrgMembersResponse)
def list_members(
    org_login: str,
    role: Literal["all", "member", "admin"] = "all",
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
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

    # Best-effort: the 2FA overlay is optional context on top of the member list
    # that already succeeded above, so any failure here (missing owner scope, or
    # a transient network/upstream error) degrades to "unavailable" rather than
    # failing the whole response -- the member/role data is still useful without it.
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
        # repos_total below is an exact count, not an estimate -- the UI's
        # "scanned X of Y" note depends on Y being accurate.
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
        # created_at is expected on every GitHub invitation object, but skip
        # rather than 500 the whole list if a malformed entry is ever missing it.
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


# ---------------------------------------------------------------------------
# Access control & risk (docs/plan.md Phase 18) -- who has elevated access, and
# who's been inactive, across the org's repos.
# ---------------------------------------------------------------------------

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
        member_logins = {m["login"] for m in client.request_paginated(f"/orgs/{org_login}/members")}
        outside_logins = {c["login"] for c in client.request_paginated(f"/orgs/{org_login}/outside_collaborators")}
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


def _last_commit_for_author(client: GitHubClient, org_login: str, repo_name: str, login: str) -> str | None:
    try:
        commits = client.request(
            "GET", f"/repos/{org_login}/{repo_name}/commits", params={"author": login, "per_page": 1}
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        return None
    if not isinstance(commits, list) or not commits:
        return None
    date = ((commits[0].get("commit") or {}).get("author") or {}).get("date")
    return date


@router.get("/github/orgs/{org_login}/inactive-members", response_model=InactiveMembersResponse)
def inactive_members(
    org_login: str,
    days: int = 30,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
    x_github_token: str | None = Header(default=None),
):
    token = _resolve_token(db, ctx, org_login, x_github_token)
    client = GitHubClient(token)
    try:
        admins_raw = client.request_paginated(f"/orgs/{org_login}/members", params={"role": "admin"})
        members_raw = client.request_paginated(f"/orgs/{org_login}/members")
        repos_raw = client.request_paginated(f"/orgs/{org_login}/repos", params={"type": "all", "sort": "pushed"})
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise _github_error(exc) from exc

    admin_logins = {m["login"] for m in admins_raw}
    sampled_repos = [r["name"] for r in repos_raw[:_MAX_REPOS_SAMPLED_FOR_ACTIVITY]]
    now = datetime.now(timezone.utc)

    def _member_last_activity(member: dict) -> tuple[str | None, str | None]:
        login = member["login"]
        for repo_name in sampled_repos:
            date = _last_commit_for_author(client, org_login, repo_name, login)
            if date:
                return repo_name, date
        return None, None

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_member_last_activity, members_raw))

    inactive: list[InactiveMember] = []
    for member, (repo_name, date) in zip(members_raw, results):
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
                    login=member["login"],
                    avatar_url=member.get("avatar_url", ""),
                    role="admin" if member["login"] in admin_logins else "member",
                    last_commit_repo=f"{org_login}/{repo_name}" if repo_name else None,
                    last_commit_days_ago=days_ago,
                )
            )

    return InactiveMembersResponse(org=org_login, sampled_repos=[f"{org_login}/{r}" for r in sampled_repos], members=inactive)
