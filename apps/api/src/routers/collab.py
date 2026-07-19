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

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from typing import Literal

from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.schemas.collab import (
    MembershipStatus,
    OrgInvitation,
    OrgInvitationsResponse,
    OrgMember,
    OrgMembersResponse,
    OutsideCollaborator,
    OutsideCollaboratorsResponse,
)
from src.services.github_client import GitHubClient, github_error as _github_error
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

# Bounds the per-repo fan-out in list_outside_collaborators -- each additional
# repo costs one more GitHub call, so large orgs are capped rather than left to
# make hundreds of sequential requests.
_MAX_REPOS_SCANNED = 50


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
