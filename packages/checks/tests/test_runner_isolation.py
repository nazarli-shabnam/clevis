"""Per-check isolation in run_all_checks."""

from unittest.mock import patch

from checks.runner import run_all_checks


def test_run_all_checks_continues_when_one_check_raises():
    with (
        patch("checks.runner._get_all_pages", return_value=[]),
        patch("checks.github_checks.OrgMFARequired.run", side_effect=RuntimeError("boom")),
        patch("checks.github_checks.BranchProtectionEnabled.run", return_value={"status": "not_applicable", "value": {}}),
        patch("checks.github_checks.SecretScanningEnabled.run", return_value={"status": "not_applicable", "value": {}}),
    ):
        result = run_all_checks(owner="acme", token="tok")

    statuses = {c["id"]: c["status"] for c in result["checks"]}
    assert statuses["organization_members_mfa_required"] == "error"
    assert statuses["repository_default_branch_protection_enabled"] == "not_applicable"
    assert statuses["repository_secret_scanning_enabled"] == "not_applicable"


def test_a_failed_repo_list_prefetch_errors_only_the_checks_that_need_repos():
    # A transient failure listing repos must not also fail org MFA, which never reads the list.
    with (
        patch("checks.runner._get_all_pages", side_effect=RuntimeError("network down")),
        patch("checks.github_checks._get", return_value={"two_factor_requirement_enabled": True}),
    ):
        result = run_all_checks(owner="acme", token="tok")

    assert result["repo_count"] == 0
    assert len(result["checks"]) == 11
    by_id = {c["id"]: c for c in result["checks"]}
    assert by_id["organization_members_mfa_required"]["status"] == "pass"
    others = [c for c in result["checks"] if c["id"] != "organization_members_mfa_required"]
    assert len(others) == 10
    assert all(c["status"] == "error" for c in others)
    assert all(c["value"] == "Check failed: could not fetch repository list" for c in others)


def test_a_failed_repo_list_prefetch_leaves_org_mfa_not_applicable_for_a_personal_account():
    with patch("checks.runner._get_all_pages", side_effect=RuntimeError("network down")):
        result = run_all_checks(owner="octocat", token="tok", account_type="User")

    by_id = {c["id"]: c for c in result["checks"]}
    assert by_id["organization_members_mfa_required"]["status"] == "not_applicable"
    assert sum(c["status"] == "error" for c in result["checks"]) == 10


def test_org_mfa_failing_on_its_own_stays_isolated_when_the_repo_list_prefetch_also_failed():
    with (
        patch("checks.runner._get_all_pages", side_effect=RuntimeError("network down")),
        patch("checks.github_checks._get", side_effect=RuntimeError("org lookup failed")),
    ):
        result = run_all_checks(owner="acme", token="tok")

    mfa = next(c for c in result["checks"] if c["id"] == "organization_members_mfa_required")
    assert mfa["status"] == "error"
    assert mfa["value"] == "Check failed: organization_members_mfa_required"


def test_only_org_mfa_opts_out_of_needing_the_repo_list():
    # Defaulting to True is the safe side: a new check is force-failed with the rest unless it
    # explicitly declares that it never reads `repos`.
    from checks.github_checks import (
        BranchProtectionEnabled,
        CodeScanningCheck,
        DefaultBranchNoForcePushCheck,
        DependabotAlertsCheck,
        OrgMFARequired,
        SecretScanningEnabled,
    )
    from checks.hygiene_checks import HYGIENE_CHECKS

    every_check = [
        OrgMFARequired,
        BranchProtectionEnabled,
        SecretScanningEnabled,
        DependabotAlertsCheck,
        CodeScanningCheck,
        DefaultBranchNoForcePushCheck,
        *HYGIENE_CHECKS,
    ]
    assert [cls.__name__ for cls in every_check if not cls.requires_repos] == ["OrgMFARequired"]
