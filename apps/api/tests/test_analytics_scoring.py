"""Analytics score treats errored checks as failures, not silent passes."""

from unittest.mock import patch

from src.services.analytics_service import get_overview


def test_overview_counts_error_checks_against_score():
    errored = {
        "checks": [
            {"status": "pass"},
            {"status": "error"},
            {"status": "fail"},
        ],
        "repo_count": 3,
    }
    with patch("src.services.analytics_service.run_all_checks", return_value=errored):
        overview = get_overview(owner="acme", token="tok")

    assert overview["failed_checks"] == 2
    assert overview["score"] == 34
    assert overview["repo_count"] == 3
