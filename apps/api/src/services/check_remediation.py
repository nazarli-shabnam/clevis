"""Turn a failing security check into a one-click "Fix this".

Each supported check_id maps to the GitHub write call(s) that enable the setting it
verifies. Only checks with a safe, unambiguous "just turn it on" fix are here --
excludes MFA enforcement (not a single idempotent call) and code-scanning alert
clearing (dismissing requires human judgement).

Requires write scopes Clevis doesn't request by default (``administration:write``,
``security_events:write``/``dependabot_alerts:write``); a 403 becomes a 400.
"""

from urllib.parse import quote

import httpx

from src.services.github_client import GitHubClient


class RemediationNotSupported(Exception):
    """The given check_id has no automated fix (see the module docstring)."""


class RemediationConflict(Exception):
    """The fix would have to overwrite existing configuration that can't be
    faithfully reconstructed through the API (see _protect_default_branch).
    Surfaced to the caller as a 409 rather than silently clobbering it."""


# A deliberately conservative default for a branch that has no protection at all:
# require one approving PR review, block force-pushes and branch deletion, and do NOT
# enforce on admins (so a repo admin can still merge an emergency fix). No required
# status checks -- Clevis can't know this repo's CI job names. See docs/self-hosting.md.
_DEFAULT_BRANCH_PROTECTION = {
    "required_status_checks": None,
    "enforce_admins": False,
    "required_pull_request_reviews": {"required_approving_review_count": 1},
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
}


def _enable_secret_scanning(client: GitHubClient, owner: str, repo: str) -> None:
    client.request(
        "PATCH",
        f"/repos/{owner}/{repo}",
        json={"security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
    )


def _protect_default_branch(client: GitHubClient, owner: str, repo: str) -> None:
    info = client.request("GET", f"/repos/{owner}/{repo}")
    branch = info.get("default_branch", "main") if isinstance(info, dict) else "main"
    # A branch name can contain slashes ("release/1.x"); keep it one path segment.
    path = f"/repos/{owner}/{repo}/branches/{quote(branch, safe='')}/protection"

    current = _get_branch_protection(client, path)
    if not current:
        # No protection at all -> apply the conservative default.
        client.request("PUT", path, json=_DEFAULT_BRANCH_PROTECTION)
        return

    # Protection already exists: carry every already-configured rule unchanged and only
    # turn force-pushes off. Re-sending _DEFAULT_BRANCH_PROTECTION would drop other rules.
    body = _preserving_put_body(current)
    body["allow_force_pushes"] = False
    client.request("PUT", path, json=body)


def _get_branch_protection(client: GitHubClient, path: str) -> dict | None:
    """Current branch protection, or None when the branch has none (GitHub 404s)."""
    try:
        result = client.request("GET", path)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    return result if isinstance(result, dict) and result else None


def _preserving_put_body(current: dict) -> dict:
    """Translate GitHub's *GET* branch-protection response into the *PUT* body
    shape, keeping every currently-enabled rule."""
    if isinstance(current.get("restrictions"), dict):
        # PUT wants restrictions as {users:[login], teams:[slug], apps:[slug]}, but GET
        # returns full objects -- getting this wrong could lock maintainers out, so refuse.
        raise RemediationConflict(
            "This branch's protection restricts who can push (specific users, teams "
            "or apps). Clevis can't rewrite that safely through the API -- turn off "
            "\"Allow force pushes\" for the default branch manually."
        )

    def _enabled(key: str) -> bool:
        value = current.get(key)
        return bool(value.get("enabled")) if isinstance(value, dict) else bool(value)

    status_checks = current.get("required_status_checks")
    if isinstance(status_checks, dict):
        checks = status_checks.get("checks")
        status_checks = {"strict": bool(status_checks.get("strict"))}
        if checks:
            # `checks` carries each check's app_id binding; `contexts` would drop it.
            status_checks["checks"] = [
                {"context": c["context"], "app_id": c.get("app_id")} for c in checks if c.get("context")
            ]
        else:
            status_checks["contexts"] = list(current["required_status_checks"].get("contexts") or [])
    else:
        status_checks = None

    reviews = current.get("required_pull_request_reviews")
    if isinstance(reviews, dict):
        out = {
            "dismiss_stale_reviews": bool(reviews.get("dismiss_stale_reviews")),
            "require_code_owner_reviews": bool(reviews.get("require_code_owner_reviews")),
            "required_approving_review_count": int(
                reviews.get("required_approving_review_count", 1)
            ),
            "require_last_push_approval": bool(reviews.get("require_last_push_approval")),
        }
        # GET returns full user/team/app objects; PUT wants logins/slugs.
        for key in ("dismissal_restrictions", "bypass_pull_request_allowances"):
            if isinstance(reviews.get(key), dict):
                out[key] = _actor_lists(reviews[key])
        reviews = out
    else:
        reviews = None

    return {
        "required_status_checks": status_checks,
        "enforce_admins": _enabled("enforce_admins"),
        "required_pull_request_reviews": reviews,
        "restrictions": None,
        "required_linear_history": _enabled("required_linear_history"),
        "allow_deletions": _enabled("allow_deletions"),
        "block_creations": _enabled("block_creations"),
        "required_conversation_resolution": _enabled("required_conversation_resolution"),
        "lock_branch": _enabled("lock_branch"),
        "allow_fork_syncing": _enabled("allow_fork_syncing"),
    }


def _actor_lists(value: dict) -> dict:
    return {
        "users": [u["login"] for u in value.get("users") or [] if u.get("login")],
        "teams": [t["slug"] for t in value.get("teams") or [] if t.get("slug")],
        "apps": [a["slug"] for a in value.get("apps") or [] if a.get("slug")],
    }


_REMEDIATIONS = {
    "repository_secret_scanning_enabled": _enable_secret_scanning,
    # repository_dependabot_alerts_clear is deliberately absent: it measures *open*
    # critical/high alerts, which enabling alerts can't fix (and would falsely report as
    # remediated).
    # Both of these are fixed by applying branch protection with force-push disabled.
    "repository_default_branch_protection_enabled": _protect_default_branch,
    "repository_default_branch_no_force_push": _protect_default_branch,
}


def supported_check_ids() -> set[str]:
    return set(_REMEDIATIONS)


def remediate(client: GitHubClient, check_id: str, owner: str, repo: str) -> None:
    """Apply the fix for ``check_id`` in ``owner/repo``. Raises RemediationNotSupported
    for a check with no automated fix; lets httpx errors from the GitHub call propagate."""
    fn = _REMEDIATIONS.get(check_id)
    if fn is None:
        raise RemediationNotSupported(check_id)
    fn(client, owner, repo)
