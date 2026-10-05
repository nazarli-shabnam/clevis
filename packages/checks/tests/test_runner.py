"""Tests for checks.runner: org repos are fetched only once."""
from unittest.mock import MagicMock, patch

import httpx

from checks.github_checks import PaginationTruncatedError
from checks.runner import run_all_checks


FAKE_REPOS = [
    {"name": "api", "default_branch": "main", "security_and_analysis": {}},
    {"name": "ui", "default_branch": "main", "security_and_analysis": {}},
]

FAKE_ORG = {"two_factor_requirement_enabled": True}

FAKE_BRANCH = {"protected": True}


def test_run_all_checks_fetches_repos_once():
    """_get_all_pages must be called exactly once, not once per check."""
    with (
        patch("checks.runner._get_all_pages", return_value=FAKE_REPOS) as mock_pages,
        patch("checks.github_checks._get") as mock_get,
    ):
        # _get is called for: org detail (MFA), N branch details (BranchProtection +
        # DefaultBranchNoForcePushCheck), and N dependabot/code-scanning alert lists.
        def fake_get(url, token):
            if "/orgs/" in url and "/repos" not in url:
                return FAKE_ORG
            if "/branches/" in url:
                return FAKE_BRANCH
            return []  # dependabot/code-scanning alert lists

        mock_get.side_effect = fake_get

        result = run_all_checks(owner="acme", token="tok")

    mock_pages.assert_called_once_with(
        "https://api.github.com", "/orgs/acme/repos", "tok"
    )

    assert len(result["checks"]) == 11
    check_ids = {c["id"] for c in result["checks"]}
    assert "organization_members_mfa_required" in check_ids
    assert "repository_default_branch_protection_enabled" in check_ids
    assert "repository_secret_scanning_enabled" in check_ids
    assert "repository_dependabot_alerts_clear" in check_ids
    assert "repository_code_scanning_alerts_clear" in check_ids
    assert "repository_default_branch_no_force_push" in check_ids

    assert result["repo_count"] == len(FAKE_REPOS)


def test_run_all_checks_passes_repos_to_checks():
    """Repos fetched by runner are forwarded — checks must not re-fetch."""
    sentinel = [{"name": "sentinel", "default_branch": "main", "security_and_analysis": {}}]

    with (
        patch("checks.runner._get_all_pages", return_value=sentinel),
        patch("checks.github_checks._get_all_pages", return_value=[]) as mock_check_pages,
        patch("checks.github_checks._get") as mock_get,
    ):
        mock_get.return_value = FAKE_ORG

        run_all_checks(owner="acme", token="tok")

    # The repo list must not be re-fetched inside the checks (repos passed in). The alert checks do
    # page through each repo's own alerts, so only the org repo-list path is forbidden.
    assert [c.args[1] for c in mock_check_pages.call_args_list if c.args[1] == "/orgs/acme/repos"] == []


def test_run_all_checks_personal_account_uses_installation_repos():
    """A personal (User-type) account's repos must come from /installation/repositories,
    not /orgs/{owner}/repos (which 404s for a User account)."""
    with (
        patch("checks.runner._get_all_pages", return_value=FAKE_REPOS) as mock_pages,
        patch("checks.github_checks._get") as mock_get,
    ):
        mock_get.side_effect = lambda url, token: FAKE_BRANCH if "/branches/" in url else []
        result = run_all_checks(owner="octocat", token="tok", account_type="User")

    mock_pages.assert_called_once_with(
        "https://api.github.com", "/installation/repositories", "tok", items_key="repositories"
    )
    mfa = next(c for c in result["checks"] if c["id"] == "organization_members_mfa_required")
    assert mfa["status"] == "not_applicable"
    # MFA's /orgs/{owner} call is skipped for a personal account.
    assert all("/orgs/" not in call.args[0] for call in mock_get.call_args_list)


def test_run_all_checks_personal_account_falls_back_to_user_repos_on_auth_mismatch():
    """A legacy PAT (not an installation token) 401s/403s on /installation/repositories --
    fall back to /user/repos."""
    forbidden = httpx.HTTPStatusError(
        "boom", request=MagicMock(), response=MagicMock(status_code=403),
    )

    def fake_pages(base_url, path, token, items_key=None):
        if path == "/installation/repositories":
            raise forbidden
        # GitHub rejects `type` together with `affiliation` (422), so the fallback must not send it.
        assert path == "/user/repos?affiliation=owner"
        assert "type=" not in path
        return FAKE_REPOS

    with (
        patch("checks.runner._get_all_pages", side_effect=fake_pages),
        patch("checks.github_checks._get", return_value=[]),
    ):
        result = run_all_checks(owner="octocat", token="tok", account_type="User")

    assert result["repo_count"] == len(FAKE_REPOS)


def test_archived_repos_are_excluded():
    repos = FAKE_REPOS + [
        {"name": "old", "default_branch": "main", "archived": True, "security_and_analysis": {}},
    ]
    with (
        patch("checks.runner._get_all_pages", return_value=repos),
        patch("checks.github_checks._get", side_effect=lambda url, token: FAKE_ORG if "/repos/" not in url else (FAKE_BRANCH if "/branches/" in url else [])),
    ):
        result = run_all_checks(owner="acme", token="tok")
    assert result["repo_count"] == 2


def test_a_truncated_ruleset_listing_errors_that_check_instead_of_passing_it():
    """A repo whose rules overflow the page cap must come out as "error", never a false "pass"."""
    def pages(base_url, path, token, items_key=None):
        if "/rules/branches/" in path:
            raise PaginationTruncatedError(path, [])
        return []

    def fake_get(url, token):
        if "/protection" in url:
            return {"allow_force_pushes": {"enabled": True}}  # classic protection allows force pushes
        if "/branches/" in url:
            return FAKE_BRANCH
        return FAKE_ORG if "/repos/" not in url else []

    with (
        patch("checks.runner._get_all_pages", return_value=FAKE_REPOS),
        patch("checks.github_checks._get_all_pages", side_effect=pages),
        patch("checks.github_checks._get", side_effect=fake_get),
    ):
        result = run_all_checks(owner="acme", token="tok")

    force_push = next(c for c in result["checks"] if c["id"] == "repository_default_branch_no_force_push")
    assert force_push["status"] == "error"
    assert force_push["value"] == "Check failed: repository_default_branch_no_force_push"
    # An unrelated check in the same run is not dragged down with it.
    mfa = next(c for c in result["checks"] if c["id"] == "organization_members_mfa_required")
    assert mfa["status"] == "pass"


def test_run_all_checks_skips_hygiene_checks_when_excluded():
    from checks.hygiene_checks import HYGIENE_CHECKS

    hygiene_ids = {cls.metadata.check_id for cls in HYGIENE_CHECKS}
    with patch("checks.runner._fetch_repos", return_value=[]), patch("checks.github_checks._get_all_pages", return_value=[]), patch(
        "checks.github_checks._get", return_value={}
    ):
        with_hygiene = run_all_checks(owner="acme", token="tok")
        without = run_all_checks(owner="acme", token="tok", include_hygiene=False)

    assert hygiene_ids <= {c["id"] for c in with_hygiene["checks"]}
    assert not hygiene_ids & {c["id"] for c in without["checks"]}
    assert len(without["checks"]) == 6
