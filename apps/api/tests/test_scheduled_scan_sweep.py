"""Tests for the scheduled-scan sweep and the cadence rules."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.core.db import Org, ScanResult
import itertools

from src.repositories import audit_repo, installation_repo, org_repo, scan_results_repo
from src.services import scheduled_scan_sweep
from src.services.scan_schedule import effective_cadence
from src.services.scheduled_scan_sweep import MAX_SCANS_PER_TICK, run_scheduled_scan_sweep
from src.services.token_resolution import NoGitHubTokenAvailable

NOW = datetime.now(timezone.utc)


def _result(owner, score=80):
    return {
        "owner": owner, "score": score, "total_checks": 1, "failed_checks": 0, "repo_count": 1,
        "checks": [{"id": "a", "title": "A", "severity": "high", "status": "pass", "scored": True}],
    }


_installation_ids = itertools.count(9000)


def _org(db, login, override=None, installed=True):
    org = org_repo.get_or_create(db, github_login=login)
    org.scheduled_scans = override
    db.commit()
    if installed:
        installation_repo.create(
            db, account_login=login, account_type="Organization", auth_mode="app",
            installation_id=next(_installation_ids), org_id=org.id,
        )
    return org


def _scan(db, org, age):
    scan_results_repo.insert(db, owner=org.github_login, score=70, total_checks=1, failed_checks=0,
                             checks=[{"id": "a", "status": "pass", "scored": True}], tenant_id=org.tenant_id)
    row = db.query(ScanResult).order_by(ScanResult.id.desc()).first()
    row.created_at = NOW - age
    db.commit()


def _run(db, cadence="daily", overview=None, token="tok"):
    overview = overview or (lambda owner, **k: _result(owner))
    with (
        patch("src.services.scan_schedule.get_config", return_value=cadence),
        patch.object(scheduled_scan_sweep, "get_overview", side_effect=overview) as get_overview,
        patch.object(scheduled_scan_sweep, "resolve_org_token", return_value=token) as resolve,
    ):
        run_scheduled_scan_sweep(db)
    return get_overview, resolve


def _actions(db, org):
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    return [r[0] for r in db.execute(text("SELECT action FROM audit_logs WHERE tenant_id = :t ORDER BY id"), {"t": org.tenant_id})]


# --- cadence rules ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override,instance,expected",
    [
        (None, "off", None), (None, "daily", "daily"), (None, "weekly", "weekly"),
        (True, "off", "weekly"), (True, "daily", "daily"),
        (False, "daily", None), (False, "off", None),
    ],
)
def test_effective_cadence(override, instance, expected):
    assert effective_cadence(override, instance) == expected


# --- sweep ------------------------------------------------------------------------------------------


def test_off_scans_nothing(db):
    org = _org(db, "ss-off")
    get_overview, _ = _run(db, cadence="off")
    get_overview.assert_not_called()
    assert _actions(db, org) == []


def test_never_scanned_org_is_scanned_stored_and_audited(db):
    org = _org(db, "ss-new")
    get_overview, resolve = _run(db)
    get_overview.assert_called_once()
    assert get_overview.call_args.kwargs["owner"] == "ss-new"
    assert resolve.call_args.kwargs["client_token"] is None  # an unattended run never uses a pasted token
    assert scan_results_repo.latest_with_checks(db, "ss-new", org.tenant_id)["score"] == 80
    assert "scan.scheduled" in _actions(db, org)


def test_recent_scan_is_not_repeated_but_a_stale_one_is(db):
    fresh, stale = _org(db, "ss-fresh"), _org(db, "ss-stale")
    _scan(db, fresh, timedelta(hours=2))
    _scan(db, stale, timedelta(days=2))
    get_overview, _ = _run(db, cadence="daily")
    assert [c.kwargs["owner"] for c in get_overview.call_args_list] == ["ss-stale"]


def test_weekly_cadence_leaves_a_three_day_old_scan_alone(db):
    org = _org(db, "ss-weekly")
    _scan(db, org, timedelta(days=3))
    get_overview, _ = _run(db, cadence="weekly")
    get_overview.assert_not_called()


def test_org_override_off_wins_over_the_instance_cadence(db):
    _org(db, "ss-ovr-off", False)
    on = _org(db, "ss-ovr-inherit", None)
    get_overview, _ = _run(db, cadence="daily")
    assert [c.kwargs["owner"] for c in get_overview.call_args_list] == [on.github_login]


def test_org_override_on_runs_even_when_the_instance_is_off(db):
    _org(db, "ss-opt-in", True)
    _org(db, "ss-no-opinion", None)
    get_overview, _ = _run(db, cadence="off")
    assert [c.kwargs["owner"] for c in get_overview.call_args_list] == ["ss-opt-in"]


def test_an_org_without_an_installation_token_is_skipped_quietly(db):
    org = _org(db, "ss-notoken")
    with (
        patch("src.services.scan_schedule.get_config", return_value="daily"),
        patch.object(scheduled_scan_sweep, "get_overview") as get_overview,
        patch.object(scheduled_scan_sweep, "resolve_org_token", side_effect=NoGitHubTokenAvailable("none")),
    ):
        run_scheduled_scan_sweep(db)
    get_overview.assert_not_called()
    assert "scan.scheduled_failed" not in _actions(db, org)


def test_a_failed_scan_is_recorded_and_not_retried_immediately(db):
    org = _org(db, "ss-fail")

    def boom(owner, **k):
        raise RuntimeError("github down")

    get_overview, _ = _run(db, overview=boom)
    assert get_overview.call_count == 1
    assert _actions(db, org) == ["scan.scheduled_started", "scan.scheduled_failed"]
    again, _ = _run(db)
    again.assert_not_called()  # inside FAILURE_RETRY


def test_one_failing_org_does_not_stop_the_others(db):
    bad, good = _org(db, "ss-a-bad"), _org(db, "ss-b-good")

    def maybe(owner, **k):
        if owner == "ss-a-bad":
            raise RuntimeError("boom")
        return _result(owner)

    _run(db, overview=maybe)
    assert scan_results_repo.latest_with_checks(db, "ss-b-good", good.tenant_id) is not None
    assert scan_results_repo.latest_with_checks(db, "ss-a-bad", bad.tenant_id) is None


def test_the_per_tick_cap_scans_the_longest_waiting_orgs_first(db):
    orgs = [_org(db, f"ss-cap-{i}") for i in range(MAX_SCANS_PER_TICK + 2)]
    for i, org in enumerate(orgs):
        _scan(db, org, timedelta(days=10 + i))  # higher index = waiting longer
    get_overview, _ = _run(db)
    scanned = [c.kwargs["owner"] for c in get_overview.call_args_list]
    assert len(scanned) == MAX_SCANS_PER_TICK
    assert scanned == [f"ss-cap-{i}" for i in range(len(orgs) - 1, len(orgs) - 1 - MAX_SCANS_PER_TICK, -1)]


def test_a_score_drop_from_a_scheduled_scan_alerts_like_a_manual_one(db):
    org = _org(db, "ss-drop")
    _scan(db, org, timedelta(days=2))
    with patch("src.services.scan_service.notifications.notify_score_drop") as notify:
        _run(db, overview=lambda owner, **k: {**_result(owner, score=40), "failed_checks": 1})
    notify.assert_called_once()
    assert notify.call_args.kwargs["actor"] == "system:scheduled_scan"


def test_orgs_without_an_installation_are_not_candidates_and_cannot_crowd_out_others(db):
    for i in range(12):
        _org(db, f"ss-noapp-{i}", installed=False)  # never scanned, so they would sort first
    good = _org(db, "ss-zz-installed")
    get_overview, resolve = _run(db)
    assert [c.kwargs["owner"] for c in get_overview.call_args_list] == [good.github_login]
    assert resolve.call_count == 1


def test_a_failure_after_the_scan_started_is_recorded_and_backs_off(db):
    org = _org(db, "ss-persist-fail")
    with patch("src.services.scheduled_scan_sweep.scan_service.persist_scan_and_alert", side_effect=RuntimeError("db")):
        get_overview, _ = _run(db)
    assert get_overview.call_count == 1
    assert _actions(db, org) == ["scan.scheduled_started", "scan.scheduled_failed"]
    again, _ = _run(db)
    again.assert_not_called()


def test_a_token_error_other_than_no_token_is_recorded_as_a_failure(db):
    org = _org(db, "ss-token-boom")
    with (
        patch("src.services.scan_schedule.get_config", return_value="daily"),
        patch.object(scheduled_scan_sweep, "get_overview") as get_overview,
        patch.object(scheduled_scan_sweep, "resolve_org_token", side_effect=RuntimeError("mint failed")),
    ):
        run_scheduled_scan_sweep(db)
    get_overview.assert_not_called()
    assert _actions(db, org) == ["scan.scheduled_failed"]


def test_an_org_whose_scan_is_marked_in_progress_is_left_alone(db):
    org = _org(db, "ss-in-progress")
    audit_repo.write(db, actor="system:scheduled_scan", action="scan.scheduled_started", target="ss-in-progress",
                     payload={}, tenant_id=org.tenant_id)
    get_overview, _ = _run(db)
    get_overview.assert_not_called()
