"""Canonical map of the GitHub App permissions Clevis needs: compares what an
installation was granted against what a feature requires, and flags which optional
automations are blocked.

Permission keys/levels are GitHub's own (REST "Get an installation" ``permissions``
object): most accept ``read``|``write``; ``workflows`` is ``write``-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Ordering matters: a higher rank satisfies a lower requirement (``write`` covers a
# ``read`` requirement; ``admin`` covers both). GitHub only ever returns these three.
_RANK: dict[str, int] = {"read": 1, "write": 2, "admin": 3}


# What every installation needs for the core security checks + dashboards to work.
# Sourced from docs/self-hosting.md's setup step (contents/metadata/administration/
# members read, the three alert-read grants for webhook ingestion, pull_requests/issues
# read for the event webhooks, and actions read for workflow runs and the cache list).
BASELINE_PERMISSIONS: dict[str, str] = {
    "metadata": "read",
    "contents": "read",
    "pull_requests": "read",
    "issues": "read",
    "actions": "read",
    "administration": "read",
    "members": "read",
    "vulnerability_alerts": "read",
    "security_events": "read",
    "secret_scanning_alerts": "read",
}


@dataclass(frozen=True)
class FeatureSpec:
    """One optional write automation and the extra permissions it needs beyond baseline."""

    label: str
    permissions: dict[str, str] = field(default_factory=dict)
    # Plain-language reason shown to the person approving the permission on GitHub.
    description: str = ""


# Keyed by a stable feature id (used in API responses and tests). Requirements come from
# each router's module docstring and docs/self-hosting.md's "Optional —" bullets.
FEATURE_PERMISSIONS: dict[str, FeatureSpec] = {
    "file_as_issue": FeatureSpec(
        "File a failed check as a GitHub issue",
        {"issues": "write"},
        "Lets Clevis open a GitHub issue for a failed security check.",
    ),
    "fix_this": FeatureSpec(
        '"Fix this" security auto-remediation',
        {"administration": "write"},
        "Lets Clevis change repository security settings for you when you click Fix this.",
    ),
    "stale_pr_nudges": FeatureSpec(
        "Stale pull-request nudges",
        {"pull_requests": "write"},
        "Lets Clevis comment on pull requests that have sat idle.",
    ),
    "bulk_branch_protection": FeatureSpec(
        "Bulk branch-protection apply",
        {"administration": "write"},
        "Lets Clevis turn on branch protection across many repositories at once.",
    ),
    "workflow_lint_autofix": FeatureSpec(
        "Workflow-policy lint auto-fix PR",
        {"contents": "write", "pull_requests": "write", "workflows": "write"},
        "Lets Clevis open a pull request that fixes issues in your workflow files.",
    ),
    "dependabot_triage": FeatureSpec(
        "Dependabot auto-triage",
        # checks/statuses read: the CI-green gate reads check-runs and commit statuses.
        {"pull_requests": "write", "contents": "write", "checks": "read", "statuses": "read"},
        "Lets Clevis approve or merge Dependabot pull requests once their checks pass.",
    ),
    "actions_cache_clear": FeatureSpec(
        "Clear GitHub Actions caches",
        {"actions": "write"},
        "Lets Clevis delete GitHub Actions caches to free up storage.",
    ),
    "workflow_dispatch": FeatureSpec(
        "Workflow dispatch (Automation page)",
        {"actions": "write"},
        "Lets Clevis start a workflow run from the Automation page.",
    ),
}


def satisfies(granted: dict[str, str] | None, required: dict[str, str]) -> bool:
    """True if ``granted`` covers every ``(permission, level)`` in ``required``."""
    return not missing_permissions(granted, required)


def missing_permissions(
    granted: dict[str, str] | None, required: dict[str, str]
) -> dict[str, str]:
    """The subset of ``required`` not met by ``granted``.

    A ``None`` or empty ``granted`` (installation never permission-checked, or the App
    genuinely has nothing) means every required permission is missing.
    """
    granted = granted or {}
    out: dict[str, str] = {}
    for perm, level in required.items():
        have = _RANK.get(granted.get(perm, ""), 0)
        if have < _RANK.get(level, 0):
            out[perm] = level
    return out


@dataclass(frozen=True)
class BlockedFeature:
    feature: str
    label: str
    missing: dict[str, str]


def blocked_features(granted: dict[str, str] | None) -> list[BlockedFeature]:
    """Every optional feature whose permissions ``granted`` does not fully cover.

    Order is the declaration order of ``FEATURE_PERMISSIONS`` so callers get a stable list.
    """
    out: list[BlockedFeature] = []
    for feature_id, spec in FEATURE_PERMISSIONS.items():
        gap = missing_permissions(granted, spec.permissions)
        if gap:
            out.append(BlockedFeature(feature=feature_id, label=spec.label, missing=gap))
    return out
