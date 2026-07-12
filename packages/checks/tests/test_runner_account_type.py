"""Tests for check runner account-type handling."""

from unittest.mock import patch

from checks.runner import run_all_checks


def test_run_all_checks_uses_user_repos_for_personal_accounts():
    repos = [{"name": "demo", "default_branch": "main", "security_and_analysis": {}}]
    with patch("checks.runner._resolve_account", return_value=("/users/alice/repos", "User")), patch(
        "checks.runner._get_all_pages", return_value=repos
    ), patch("checks.github_checks._get") as mock_get:
        mock_get.return_value = {"protected": True}
        report = run_all_checks(owner="alice", token="tok")

    mfa = next(item for item in report["checks"] if item["id"] == "organization_members_mfa_required")
    assert mfa["status"] == "not_applicable"
    assert report["repo_count"] == 1
