"""Identity checks built from data Clevis already stores, not from GitHub calls.

``org_members.two_factor_enabled`` and ``repo_collaborators`` are kept up to date by the worker (the hourly
membership reconcile and the ``member``/``organization`` webhooks), so these checks cost no GitHub requests.
The checks package has no database access, so the caller (the API's scan) reads those rows and hands them in.

Both are informational, like the hygiene checks: shown with the results, but only scored for an org that
opts into scoring them.
"""

from __future__ import annotations

from checks.base import Check, CheckMetadata


class MembersWithout2FA(Check):
    """Fails when any org member is recorded without two-factor authentication.

    An org can require 2FA (``OrgMFARequired``) while individual members have not enabled it yet, for example
    members added before the requirement, or an owner exempted. ``two_factor_enabled`` is NULL when the 2FA
    overlay was unavailable (the token lacks org-owner scope), which is unknown, not a pass."""

    metadata = CheckMetadata(
        check_id="organization_members_2fa_enabled",
        title="All organization members have 2FA enabled",
        severity="medium",
        remediation="Ask members without two-factor authentication to enable it in their GitHub security settings.",
        informational=True,
    )
    requires_repos = False

    def __init__(self, members: list[dict]):
        # Each: {"login": str, "two_factor_enabled": bool | None}
        self._members = members

    def run(self, owner, token, base_url="https://api.github.com", repos=None, account_type="Organization") -> dict:
        total = len(self._members)
        enabled = sum(1 for m in self._members if m.get("two_factor_enabled") is True)
        disabled = sum(1 for m in self._members if m.get("two_factor_enabled") is False)
        unknown = total - enabled - disabled
        value = {"enabled": enabled, "total": total, "unknown": unknown}
        if total == 0 or unknown == total:
            # No roster yet, or 2FA status was never available: nothing was assessed.
            return {"status": "not_applicable", "value": value}
        return {"status": "fail" if disabled else "pass", "value": value}


class OutsideCollaboratorsWithAdmin(Check):
    """Fails when an outside collaborator (not an org member) has admin permission on a repository.

    Based on the ``repo_collaborators`` rows Clevis has recorded, which come from ``member`` webhooks and the
    reconcile that classifies them as outside collaborators, so it covers the collaborators Clevis has learned
    about, not necessarily every one on GitHub."""

    metadata = CheckMetadata(
        check_id="repository_no_outside_collaborator_admins",
        title="No outside collaborators have admin access",
        severity="high",
        remediation="Reduce outside collaborators' repository permission below admin, or remove them.",
        informational=True,
    )
    requires_repos = False

    def __init__(self, collaborators: list[dict]):
        # Each: {"repo": str, "login": str, "permission": str, "is_outside_collaborator": bool | None}
        self._collaborators = collaborators

    def run(self, owner, token, base_url="https://api.github.com", repos=None, account_type="Organization") -> dict:
        if not self._collaborators:
            return {"status": "not_applicable", "value": "No collaborator data has been recorded yet."}
        outside = [c for c in self._collaborators if c.get("is_outside_collaborator") is True]
        if not outside:
            return {"status": "pass", "value": "No outside collaborators have been recorded."}
        admins = [c for c in outside if c.get("permission") == "admin"]
        value = {"checked": len(outside), "missing": len(admins)}
        return {"status": "fail" if admins else "pass", "value": value}


def identity_checks(stored: dict) -> list[Check]:
    """The identity checks for one org's stored data: ``{"members": [...], "collaborators": [...]}``."""
    return [MembersWithout2FA(stored.get("members", [])), OutsideCollaboratorsWithAdmin(stored.get("collaborators", []))]
