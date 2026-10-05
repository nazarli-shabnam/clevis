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

    # not_applicable is excluded from both numerator and denominator.
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
    # total_checks must exclude not_applicable too, or the UI's "passed = total - failed" lies.
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

    # total_checks must reflect the same scored set as failed_checks/score, not the raw list.
    assert overview["total_checks"] == 2
    assert overview["failed_checks"] == 1


def test_informational_checks_do_not_affect_the_score_unless_opted_in():
    from unittest.mock import patch

    from src.services.analytics_service import get_overview

    def report():
        # fresh dicts per scan: get_overview stamps "scored" onto them in place
        checks = [
            {"id": "a", "status": "pass"},
            {"id": "hygiene", "status": "fail", "informational": True},
        ]
        return {"checks": checks, "repo_count": 1}

    with patch("src.services.analytics_service.run_all_checks", side_effect=lambda **_: report()):
        with patch("src.services.analytics_service.get_config", return_value="false"):
            off = get_overview("acme", "t")
        with patch("src.services.analytics_service.get_config", return_value="true"):
            on = get_overview("acme", "t")
    assert (off["score"], off["total_checks"], off["failed_checks"]) == (100, 1, 0)
    assert (on["score"], on["total_checks"], on["failed_checks"]) == (50, 2, 1)
    assert [c["scored"] for c in off["checks"]] == [True, False]
    assert [c["scored"] for c in on["checks"]] == [True, True]


def test_overview_only_runs_hygiene_checks_when_they_are_scored():
    report = {"checks": [{"status": "pass"}], "repo_count": 1}
    for configured, expected in (("false", False), ("true", True)):
        with patch("src.services.analytics_service.get_config", return_value=configured), patch(
            "src.services.analytics_service.run_all_checks", return_value=report
        ) as run:
            get_overview(owner="acme", token="tok")
        assert run.call_args.kwargs["include_hygiene"] is expected
