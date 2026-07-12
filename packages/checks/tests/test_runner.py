"""Tests for checks.runner — verifies B-05 fix: org repos are fetched only once."""
from unittest.mock import MagicMock, patch

from checks.runner import run_all_checks


FAKE_REPOS = [
    {"name": "api", "default_branch": "main", "security_and_analysis": {}},
    {"name": "ui", "default_branch": "main", "security_and_analysis": {}},
]

FAKE_ORG = {"two_factor_requirement_enabled": True}

FAKE_BRANCH = {"protected": True}


def test_run_all_checks_fetches_repos_once():
    """_get_all_pages must be called exactly once, not once per check (B-05)."""
    with (
        patch("checks.runner._get_all_pages", return_value=FAKE_REPOS) as mock_pages,
        patch("checks.github_checks._get") as mock_get,
    ):
        # _get is called for: org detail (MFA) + N branch details (BranchProtection)
        mock_get.side_effect = lambda url, token: (
            FAKE_ORG if "/orgs/" in url and "/repos" not in url and "/branches" not in url
            else FAKE_BRANCH
        )

        result = run_all_checks(owner="acme", token="tok")

    # The critical assertion: repos fetched once, not twice (B-05)
    mock_pages.assert_called_once_with(
        "https://api.github.com", "/orgs/acme/repos", "tok"
    )

    # Sanity: all 3 checks returned results
    assert len(result["checks"]) == 3
    check_ids = {c["id"] for c in result["checks"]}
    assert "organization_members_mfa_required" in check_ids
    assert "repository_default_branch_protection_enabled" in check_ids
    assert "repository_secret_scanning_enabled" in check_ids

    # repo_count is surfaced so callers (e.g. the analytics overview) don't have
    # to re-fetch the org's repo list just to get a count.
    assert result["repo_count"] == len(FAKE_REPOS)


def test_run_all_checks_passes_repos_to_checks():
    """Repos fetched by runner are forwarded — checks must not re-fetch."""
    sentinel = [{"name": "sentinel", "default_branch": "main", "security_and_analysis": {}}]

    with (
        patch("checks.runner._get_all_pages", return_value=sentinel),
        patch("checks.github_checks._get_all_pages") as mock_check_pages,
        patch("checks.github_checks._get") as mock_get,
    ):
        mock_get.return_value = FAKE_ORG

        run_all_checks(owner="acme", token="tok")

    # _get_all_pages inside github_checks must NOT be called (repos passed in)
    mock_check_pages.assert_not_called()
