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
    # 2 of 3 failed is exactly 33.3: the score floors, it never rounds up to 34.
    assert overview["score"] == 33
    assert overview["repo_count"] == 3


def test_overview_score_floors_with_exact_integer_math():
    def score(n_pass, n_fail):
        report = {"checks": [{"status": "pass"}] * n_pass + [{"status": "fail"}] * n_fail, "repo_count": 1}
        with patch("src.services.analytics_service.run_all_checks", return_value=report):
            return get_overview(owner="acme", token="tok")["score"]

    assert score(5, 1) == 83  # 1 of 6 failed: exact 83.3, was reported as 84
    assert score(71, 29) == 71  # 0.29 * 100 is 28.999... in floats, which used to give 72
    assert score(0, 4) == 0
    assert score(4, 0) == 100


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


def test_overview_all_not_applicable_checks_has_no_score():
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
    # Nothing was assessed, so it must not read as a perfect score.
    assert overview["score"] is None
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


def _report_with_hygiene():
    return {
        "checks": [
            {"status": "pass"},
            {"status": "fail", "informational": True},
        ],
        "repo_count": 1,
    }


def test_overview_explicit_score_hygiene_overrides_the_instance_setting():
    with patch("src.services.analytics_service.get_config", return_value="false"):
        with patch("src.services.analytics_service.run_all_checks", return_value=_report_with_hygiene()):
            on = get_overview(owner="acme", token="t", score_hygiene=True)
        with patch("src.services.analytics_service.run_all_checks", return_value=_report_with_hygiene()):
            followed = get_overview(owner="acme", token="t")  # None -> instance (off)
    assert on["total_checks"] == 2 and on["score"] == 50 and [c["scored"] for c in on["checks"]] == [True, True]
    assert followed["total_checks"] == 1 and followed["score"] == 100
    assert [c["scored"] for c in followed["checks"]] == [True, False]

    with patch("src.services.analytics_service.get_config", return_value="true"):
        with patch("src.services.analytics_service.run_all_checks", return_value=_report_with_hygiene()):
            off = get_overview(owner="acme", token="t", score_hygiene=False)
    assert off["total_checks"] == 1 and off["score"] == 100


def test_org_scores_hygiene_prefers_its_own_setting_and_falls_back_to_the_instance():
    from types import SimpleNamespace

    from src.services.analytics_service import org_scores_hygiene

    with patch("src.services.analytics_service.get_config", return_value="true"):
        assert org_scores_hygiene(SimpleNamespace(score_hygiene_checks=None)) is True
        assert org_scores_hygiene(SimpleNamespace(score_hygiene_checks=False)) is False
    with patch("src.services.analytics_service.get_config", return_value="false"):
        assert org_scores_hygiene(SimpleNamespace(score_hygiene_checks=None)) is False
        assert org_scores_hygiene(SimpleNamespace(score_hygiene_checks=True)) is True
