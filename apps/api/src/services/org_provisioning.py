"""Auto-provisions and reconciles Clevis org memberships from verified GitHub org status.

Runs at OAuth login time, using the transiently-available live user token. Fetches the
user's org memberships (role included) from GitHub in one call, then:

1. For every org where they're currently a GitHub admin, get-or-creates the Clevis Org
   and grants/refreshes an admin OrgMembership -- GitHub already vouches for admins, so
   no invite is required.
2. Reconciles every existing GitHub-sourced Clevis membership against the fresh GitHub
   state: demotes to "member" if GitHub now says member, deletes the row if GitHub no
   longer lists the org for this user at all -- so admin privilege can't outlive its GitHub
   grant. Invite-sourced memberships are Clevis's own grant and are left alone.

Non-admin GitHub members with no prior Clevis membership are never auto-added; they only
gain access through the invite-accept flow. Best-effort: any GitHub API failure is
logged and swallowed so it never blocks login.
"""

import logging

from sqlalchemy.orm import Session

from src.core.db import Org, User, set_session_user
from src.repositories import audit_repo, org_membership_repo, org_repo, tenant_repo
from src.services import github_oauth

logger = logging.getLogger(__name__)


def connect_admin_org_from_token(
    db: Session, user: User, org_login: str, user_token: str
) -> Org | None:
    """Single-org version of :func:`sync_org_admin_memberships` for the legacy-PAT path:
    when a workspace admin saves a PAT for an org Clevis has never connected, use that PAT
    to establish the ``Org`` + admin membership so the ``/orgs/{org}/...`` dashboard pages
    work instead of 404ing.

    Same authorization policy as the OAuth path: only connects when GitHub reports the
    caller as an admin/owner -- pasting a PAT must not be a back door around the
    invite-accept flow. Returns the connected ``Org``, or ``None`` if it can't be resolved.
    **Never raises** — the token save must succeed regardless.
    """
    try:
        memberships = github_oauth.list_user_org_memberships(user_token)
    except Exception as exc:  # noqa: BLE001 -- best-effort; a bad response must not 500 the save
        logger.info("PAT org auto-connect skipped for %s: %s", org_login, exc)
        return None

    match = next(
        (m for m in memberships if m.login.lower() == org_login.lower() and m.role == "admin"),
        None,
    )
    if match is None:
        return None

    set_session_user(db, user.id)
    org = org_repo.get_or_create(db, github_login=match.login, github_org_id=match.github_org_id)
    membership = org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin")
    if membership.role != "admin":
        org_membership_repo.update_role(db, org_id=org.id, user_id=user.id, role="admin")
    audit_repo.write(
        db, user.email, "token.org_autolinked", match.login, {"role": "admin"},
        tenant_id=org.tenant_id,
    )
    return org


def sync_org_admin_memberships(db: Session, user: User, user_token: str) -> None:
    # Every membership write below is for `user` (never a different user), so this alone
    # satisfies the self-access RLS check regardless of which org/tenant each write
    # targets. Defensive here rather than relying on the caller having already set it.
    set_session_user(db, user.id)
    try:
        memberships = github_oauth.list_user_org_memberships(user_token)
    except Exception:  # noqa: BLE001 -- best-effort; must never block login
        # Not just httpx.HTTPError: a shape drift in GitHub's response (missing
        # `organization`/`id`/`login`/`role` key, non-JSON body) raises KeyError/
        # TypeError/ValueError too. Matches connect_admin_org_from_token's own catch above.
        logger.warning(
            "Failed to list GitHub org memberships for user %s during org provisioning", user.id, exc_info=True
        )
        return

    gh_role_by_org_id = {m.github_org_id: m.role for m in memberships}

    for gh_membership in memberships:
        if gh_membership.role != "admin":
            continue
        org = org_repo.get_or_create(db, github_login=gh_membership.login, github_org_id=gh_membership.github_org_id)
        existing = org_membership_repo.get(db, org_id=org.id, user_id=user.id)
        if existing is not None and existing.role == "admin":
            continue
        org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="admin")
        if existing is not None and existing.source != "github":
            # The admin role now comes from GitHub, so GitHub must be able to take it back:
            # an invite-sourced row promoted here would otherwise survive GitHub removal as an
            # admin. (Fail-closed: losing GitHub admin then revokes the row; re-invite if needed.)
            org_membership_repo.set_source(db, org_id=org.id, user_id=user.id, source="github")
        _audit(db, user, "membership.github_granted", org, {"role": "admin"})

    for org, membership in tenant_repo.list_org_memberships_for_user(db, user.id):
        if org.github_org_id is None or membership.source != "github":
            continue
        gh_role = gh_role_by_org_id.get(org.github_org_id)
        if gh_role is None:
            org_membership_repo.delete(db, org_id=org.id, user_id=user.id)
            _audit(db, user, "membership.github_revoked", org, {"previous_role": membership.role})
        elif gh_role == "member" and membership.role != "member":
            org_membership_repo.update_role(db, org_id=org.id, user_id=user.id, role="member")
            _audit(db, user, "membership.github_demoted", org, {"role": "member"})


def _audit(db: Session, user: User, action: str, org: Org, payload: dict) -> None:
    audit_repo.write(db, user.email, action, org.github_login, payload, tenant_id=org.tenant_id)
