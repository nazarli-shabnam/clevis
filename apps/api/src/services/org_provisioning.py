"""
Auto-provisions Clevis orgs/admin-memberships from verified GitHub org-admin status.

Runs at OAuth login time, where a live user token is transiently available (see
src/routers/github_auth.py). For every GitHub org the user belongs to, checks whether
they're an org admin there; if so, get-or-creates the Clevis Org and an admin
OrgMembership for them. This covers both "first person to connect this org" and "another
verified GitHub admin signing in later" — GitHub already vouches for admins, so no invite
is required. Non-admin GitHub members are never touched here; they only gain access
through the explicit invite-accept flow.

Best-effort: any GitHub API failure is logged and swallowed so it never blocks login.
"""

import logging

import httpx

from sqlalchemy.orm import Session

from src.core.db import User
from src.repositories import org_membership_repo, org_repo
from src.services import github_oauth

logger = logging.getLogger(__name__)


def sync_org_admin_memberships(db: Session, user: User, user_token: str, github_username: str) -> None:
    try:
        github_orgs = github_oauth.list_user_orgs(user_token)
    except httpx.HTTPError:
        logger.warning("Failed to list GitHub orgs for user %s during org provisioning", user.id, exc_info=True)
        return

    for gh_org in github_orgs:
        try:
            role = github_oauth.get_org_membership_role(user_token, gh_org.login, github_username)
        except httpx.HTTPError:
            logger.warning(
                "Failed to check GitHub org membership role for %s in %s", github_username, gh_org.login,
                exc_info=True,
            )
            continue
        if role != "admin":
            continue
        org = org_repo.get_or_create(db, github_login=gh_org.login, github_org_id=gh_org.github_org_id)
        org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin")
