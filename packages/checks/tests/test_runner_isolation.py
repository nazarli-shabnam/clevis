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


def test_run_all_checks_degrades_to_error_results_when_the_repo_list_prefetch_fails():
    # Regression test for issue #224 item 4: the repo-list prefetch used to sit outside
    # the per-check try/except, so a failure there raised out of run_all_checks entirely
    # instead of degrading the same way an individual check failure does.
    with patch("checks.runner._get_all_pages", side_effect=RuntimeError("network down")):
        result = run_all_checks(owner="acme", token="tok")

    assert result["repo_count"] == 0
    assert len(result["checks"]) == 6
    assert all(c["status"] == "error" for c in result["checks"])
