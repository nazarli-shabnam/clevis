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


def test_overview_excludes_not_applicable_checks_from_score():
    report = {
        "checks": [
            {"status": "pass"},
            {"status": "not_applicable"},
            {"status": "fail"},
        ],
        "repo_count": 0,
    }
    with patch("src.services.analytics_service.run_all_checks", return_value=report):
        overview = get_overview(owner="acme", token="tok")

    # not_applicable is excluded from both the numerator and denominator —
    # scored against {pass, fail} only, not {pass, not_applicable, fail}.
    assert overview["failed_checks"] == 1
    assert overview["score"] == 50


def test_overview_all_not_applicable_checks_scores_100():
    report = {
        "checks": [
            {"status": "not_applicable"},
            {"status": "not_applicable"},
        ],
        "repo_count": 0,
    }
    with patch("src.services.analytics_service.run_all_checks", return_value=report):
        overview = get_overview(owner="acme", token="tok")

    assert overview["failed_checks"] == 0
    assert overview["score"] == 100
    # total_checks must also exclude not_applicable entries — otherwise the
    # UI's `passed = total - failed` reads "2 passed" for an org where
    # nothing was actually evaluated.
    assert overview["total_checks"] == 0


def test_overview_total_checks_excludes_not_applicable():
    report = {
        "checks": [
            {"status": "pass"},
            {"status": "fail"},
            {"status": "not_applicable"},
            {"status": "not_applicable"},
        ],
        "repo_count": 1,
    }
    with patch("src.services.analytics_service.run_all_checks", return_value=report):
        overview = get_overview(owner="acme", token="tok")

    # 4 checks total, but only 2 are evaluable (pass/fail); total_checks
    # must reflect the same scored set used for failed_checks/score, not
    # the raw, unfiltered checks list.
    assert overview["total_checks"] == 2
    assert overview["failed_checks"] == 1
