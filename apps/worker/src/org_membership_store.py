"""org_members + repo_collaborators write path (current state, not a log).

Webhook delivery order isn't guaranteed and reclaims can retry late, so every write is
guarded by the event's received_at against the row's added_at/granted_at. Known gap: a
"removed" processed before an older "added" for a login with no row is still clobbered by
that late "added" (would need tombstones).
"""

import json
import time
from collections.abc import Callable
from datetime import datetime

import psycopg

# Hashed the same way as apps/api's sweep_lock.py namespace so the lock families can't collide.
_LOCK_NAMESPACE = "org_membership_reconcile"


def acquire_tenant_lock(conn: psycopg.Connection, tenant_id: int) -> datetime:
    """Session-level advisory lock serializing one tenant's org-membership writes.

    Keeps the webhook path from interleaving with the reconcile job's roster-fetch-to-snapshot
    window, where a snapshot could resurrect or wipe members. Session-scoped (not xact) because
    the reconcile side holds it across an HTTP call before any transaction; callers MUST call
    release_tenant_lock in a finally.

    Returns the database's wall clock (``clock_timestamp()``, not the transaction-start ``now()``)
    read once the lock is held. Webhook ``received_at`` is stamped by the database, so a snapshot
    time that must compare against it has to come from the same clock, not the worker's.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(hashtext(%s), %s)", (_LOCK_NAMESPACE, tenant_id))
        cur.execute("SELECT clock_timestamp()")
        locked_at = cur.fetchone()[0]
    conn.commit()
    return locked_at


def try_acquire_tenant_lock(
    conn: psycopg.Connection,
    tenant_id: int,
    *,
    timeout_s: float,
    poll_s: float = 0.25,
    on_wait: Callable[[], None] | None = None,
) -> datetime | None:
    """acquire_tenant_lock without the unbounded block: poll ``pg_try_advisory_lock`` for up to
    ``timeout_s`` and return None if another connection (typically a long reconcile job) still holds it.

    For callers that serve many tenants on one thread and must not stall all of them behind one tenant's
    lock. ``on_wait`` runs between polls (e.g. to refresh a liveness heartbeat). On success the lock is
    held and must be released with release_tenant_lock, exactly as for acquire_tenant_lock."""
    deadline = time.monotonic() + timeout_s
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), %s)", (_LOCK_NAMESPACE, tenant_id))
            got = cur.fetchone()[0]
            locked_at = None
            if got:
                cur.execute("SELECT clock_timestamp()")
                locked_at = cur.fetchone()[0]
        conn.commit()
        if got:
            return locked_at
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if on_wait is not None:
            on_wait()
        time.sleep(min(poll_s, remaining))


def release_tenant_lock(conn: psycopg.Connection, tenant_id: int) -> None:
    """Release acquire_tenant_lock's lock; safe after a rollback (session-level hold)."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)", (_LOCK_NAMESPACE, tenant_id))
    conn.commit()


def upsert_org_member(
    cur: psycopg.Cursor, *, tenant_id: int, login: str, avatar_url: str, role: str, added_at: datetime
) -> None:
    """Upsert an org_members row keyed on (tenant_id, login).

    `role` is only set on INSERT: the reconciliation poll may have corrected it since this
    event was generated, so a conflict must not overwrite it with a stale snapshot.
    """
    cur.execute(
        """
        INSERT INTO org_members (tenant_id, login, avatar_url, role, added_at)
        VALUES (%(tenant_id)s, %(login)s, %(avatar_url)s, %(role)s, %(added_at)s)
        ON CONFLICT (tenant_id, login)
        DO UPDATE SET avatar_url = EXCLUDED.avatar_url, added_at = EXCLUDED.added_at
        WHERE EXCLUDED.added_at >= org_members.added_at
        """,
        {"tenant_id": tenant_id, "login": login, "avatar_url": avatar_url, "role": role, "added_at": added_at},
    )


def remove_org_member(cur: psycopg.Cursor, *, tenant_id: int, login: str, event_received_at: datetime) -> None:
    """Delete an org_members row unless the removal is older than its `added_at`."""
    cur.execute(
        "DELETE FROM org_members WHERE tenant_id = %(tenant_id)s AND login = %(login)s AND added_at <= %(event_received_at)s",
        {"tenant_id": tenant_id, "login": login, "event_received_at": event_received_at},
    )


def revoke_github_membership(
    cur: psycopg.Cursor,
    *,
    tenant_id: int,
    github_user_id: int,
    login: str,
    actor: str = "github-webhook",
    reason: str = "member_removed",
) -> None:
    """Delete the GitHub-sourced Clevis membership of a user GitHub just removed from the org,
    auditing it in the same transaction (same action the OAuth-login reconcile writes).

    Invite-sourced memberships are Clevis's own grant (e.g. outside contractors) and survive.
    Caller must have set app.tenant_id (memberships' RLS tenant clause)."""
    cur.execute(
        "DELETE FROM memberships WHERE tenant_id = %(tenant_id)s AND source = 'github' "
        "AND user_id IN (SELECT id FROM users WHERE github_user_id = %(gh_id)s) RETURNING role",
        {"tenant_id": tenant_id, "gh_id": github_user_id},
    )
    for (role,) in cur.fetchall():
        cur.execute(
            "INSERT INTO audit_logs (actor, action, target, payload, tenant_id, created_at) "
            "VALUES (%s, 'membership.github_revoked', %s, %s, %s, NOW())",
            (actor, login, json.dumps({"previous_role": role, "reason": reason}), tenant_id),
        )


def upsert_repo_collaborator(
    cur: psycopg.Cursor,
    *,
    tenant_id: int,
    repo: str,
    login: str,
    permission: str,
    is_outside_collaborator: bool | None,
    granted_at: datetime,
) -> None:
    """Upsert a repo_collaborators row keyed on (tenant_id, repo, login); source is 'direct'.

    `is_outside_collaborator` is only set on INSERT so a conflict doesn't clobber a value
    the reconciliation poll filled in.
    """
    cur.execute(
        """
        INSERT INTO repo_collaborators
            (tenant_id, repo, login, permission, source, is_outside_collaborator, granted_at)
        VALUES (%(tenant_id)s, %(repo)s, %(login)s, %(permission)s, 'direct',
                %(is_outside_collaborator)s, %(granted_at)s)
        ON CONFLICT (tenant_id, repo, login)
        DO UPDATE SET permission = EXCLUDED.permission, granted_at = EXCLUDED.granted_at
        WHERE EXCLUDED.granted_at >= repo_collaborators.granted_at
        """,
        {
            "tenant_id": tenant_id,
            "repo": repo,
            "login": login,
            "permission": permission,
            "is_outside_collaborator": is_outside_collaborator,
            "granted_at": granted_at,
        },
    )


def remove_repo_collaborator(cur: psycopg.Cursor, *, tenant_id: int, repo: str, login: str, event_received_at: datetime) -> None:
    """Delete a repo_collaborators row unless the removal is older than its `granted_at`."""
    cur.execute(
        "DELETE FROM repo_collaborators WHERE tenant_id = %(tenant_id)s AND repo = %(repo)s AND login = %(login)s "
        "AND granted_at <= %(event_received_at)s",
        {"tenant_id": tenant_id, "repo": repo, "login": login, "event_received_at": event_received_at},
    )


def reconcile_org_members(cur: psycopg.Cursor, *, tenant_id: int, members: list[dict], synced_at: datetime) -> None:
    """Full-roster reconciliation write: the poll's view is authoritative.

    `role`/`avatar_url` are always overwritten; `two_factor_enabled` uses COALESCE since a
    None means the best-effort overlay failed. Rows for logins not in `members` are deleted
    (missed removals). `added_at` is preserved for existing rows, else set to `synced_at`.
    An empty `members` list is a no-op, since an org always has an owner."""
    if not members:
        return

    seen_logins = [m["login"] for m in members]
    for member in members:
        cur.execute(
            """
            INSERT INTO org_members (tenant_id, login, avatar_url, role, two_factor_enabled, added_at)
            VALUES (%(tenant_id)s, %(login)s, %(avatar_url)s, %(role)s, %(two_factor_enabled)s, %(synced_at)s)
            ON CONFLICT (tenant_id, login) DO UPDATE SET
                avatar_url = EXCLUDED.avatar_url,
                role = EXCLUDED.role,
                two_factor_enabled = COALESCE(EXCLUDED.two_factor_enabled, org_members.two_factor_enabled)
            """,
            {
                "tenant_id": tenant_id,
                "login": member["login"],
                "avatar_url": member["avatar_url"],
                "role": member["role"],
                "two_factor_enabled": member.get("two_factor_enabled"),
                "synced_at": synced_at,
            },
        )

    cur.execute(
        "DELETE FROM org_members WHERE tenant_id = %(tenant_id)s AND NOT (login = ANY(%(seen_logins)s))",
        {"tenant_id": tenant_id, "seen_logins": seen_logins},
    )


def reconcile_repo_collaborator_outside_status(
    cur: psycopg.Cursor,
    *,
    tenant_id: int,
    member_logins: set[str],
    outside_logins: set[str],
    synced_at: datetime,
    prune: bool,
) -> None:
    """Fill in is_outside_collaborator on existing repo_collaborators rows, and drop rows for logins that are
    neither an org member nor an outside collaborator (a missed `member` removed webhook would otherwise keep
    their old permission, e.g. an outside admin, forever).

    Same heuristic as collab.py's permission_audit: outside iff not an org member or in
    outside_logins. Two-step UPDATE (mark all outside, then correct members). Never creates rows. The delete
    spares rows granted at or after `synced_at`: a grant webhook processed since the roster was read is newer
    than it. `prune` must only be true for a roster known to be complete: without the 2FA overlay the member
    list may be the public-only one, and deleting by it would drop real members' rows."""
    if not member_logins:
        # An empty roster is never legitimate; bail before the unconditional UPDATE marks
        # everyone outside.
        return
    if prune:
        cur.execute(
            "DELETE FROM repo_collaborators WHERE tenant_id = %(tenant_id)s AND granted_at < %(synced_at)s "
            "AND NOT (login = ANY(%(logins)s))",
            {"tenant_id": tenant_id, "synced_at": synced_at, "logins": list(member_logins | outside_logins)},
        )
    cur.execute(
        "UPDATE repo_collaborators SET is_outside_collaborator = TRUE WHERE tenant_id = %(tenant_id)s",
        {"tenant_id": tenant_id},
    )
    direct_members = member_logins - outside_logins
    if direct_members:
        cur.execute(
            "UPDATE repo_collaborators SET is_outside_collaborator = FALSE "
            "WHERE tenant_id = %(tenant_id)s AND login = ANY(%(logins)s)",
            {"tenant_id": tenant_id, "logins": list(direct_members)},
        )


def reconcile_github_memberships(cur: psycopg.Cursor, *, tenant_id: int, members: list[dict]) -> None:
    """Apply the full roster to the org's GitHub-sourced Clevis memberships: revoke the access of a user
    GitHub no longer lists, and demote an admin that GitHub now lists as a plain member.

    This is the safety net for a missed ``organization.member_removed`` webhook; it reuses the webhook
    path's revoke (and its ``membership.github_revoked`` audit row, reason ``reconcile``) and mirrors the
    sign-in reconcile (``org_provisioning.sync_org_admin_memberships``), which also never promotes.
    Invite-sourced memberships are Clevis's own grant and are left alone. Matching is by
    ``users.github_user_id``; users without one can't be matched and are left alone.

    Fails closed: an empty roster, one with no admin (an org always has an owner, so it was probably a
    public-only listing), or one with an entry lacking a GitHub id changes nothing, since a wrong answer
    here locks real people out. Caller must have set app.tenant_id."""
    ids_by_role: dict[str, list[int]] = {"admin": [], "member": []}
    for member in members:
        github_id = member.get("github_user_id")
        if not isinstance(github_id, int):
            return
        ids_by_role.get(member["role"], ids_by_role["member"]).append(github_id)
    if not ids_by_role["admin"]:
        return
    roster_ids = ids_by_role["admin"] + ids_by_role["member"]

    cur.execute(
        "SELECT DISTINCT u.github_user_id FROM memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.tenant_id = %(tenant_id)s AND m.source = 'github' AND u.github_user_id IS NOT NULL "
        "AND NOT (u.github_user_id = ANY(%(roster_ids)s))",
        {"tenant_id": tenant_id, "roster_ids": roster_ids},
    )
    for (github_user_id,) in cur.fetchall():
        revoke_github_membership(
            cur,
            tenant_id=tenant_id,
            github_user_id=github_user_id,
            login=f"github_user:{github_user_id}",
            actor="github-reconcile",
            reason="reconcile",
        )

    if ids_by_role["member"]:
        cur.execute(
            "UPDATE memberships m SET role = 'member' FROM users u "
            "WHERE u.id = m.user_id AND m.tenant_id = %(tenant_id)s AND m.source = 'github' "
            "AND m.role = 'admin' AND u.github_user_id = ANY(%(member_ids)s) RETURNING u.github_user_id",
            {"tenant_id": tenant_id, "member_ids": ids_by_role["member"]},
        )
        for (github_user_id,) in cur.fetchall():
            cur.execute(
                "INSERT INTO audit_logs (actor, action, target, payload, tenant_id, created_at) "
                "VALUES ('github-reconcile', 'membership.github_demoted', %s, %s, %s, NOW())",
                (f"github_user:{github_user_id}", json.dumps({"role": "member", "reason": "reconcile"}), tenant_id),
            )
