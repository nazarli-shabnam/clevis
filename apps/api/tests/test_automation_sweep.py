"""Scheduled stale-PR nudges and Dependabot auto-triage (#833): the sweep's rails and the org controls."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import automation_settings_repo, org_membership_repo, org_repo
from src.routers.automation_schedules import router as schedules_router
from src.services import automation_sweep, dependabot_triage, pr_nudge
from src.services.automation_sweep import run_automation_sweep
from src.services.token_resolution import NoGitHubTokenAvailable


@pytest.fixture()
def org(db):
    org = org_repo.get_or_create(db, github_login="acme")
    org.pr_nudge_schedule = True
    org.dependabot_triage_schedule = True
    db.commit()
    automation_settings_repo.upsert(db, org.tenant_id, "acme/api", "pr_nudge", enabled=True)
    automation_settings_repo.upsert(db, org.tenant_id, "acme/off", "pr_nudge", enabled=False)
    automation_settings_repo.upsert(
        db, org.tenant_id, "acme/api", "dependabot_triage", enabled=True, mode="approve_and_merge", extra={"merge_method": "rebase"}
    )
    return org


def _sweep(*, instance="off", mode="comment", token="itok", installed=True, **extra_patches):
    """Run the sweep with config, installation and token stubbed; returns the patched collaborators."""
    config = {"pr_nudge_cadence": instance, "dependabot_triage_cadence": instance, "pr_nudge_mode": mode}
    stack = ExitStack()
    mocks = {
        "nudge": stack.enter_context(patch("src.services.automation_sweep.pr_nudge.run_nudge_sweep", return_value=[])),
        "triage": stack.enter_context(patch("src.services.automation_sweep.dependabot_triage.triage", return_value=[])),
        "client": stack.enter_context(patch("src.services.automation_sweep.GitHubClient")),
        "notify": stack.enter_context(patch("src.services.automation_sweep.notifications.notify_stale_prs")),
    }
    stack.enter_context(patch("src.services.automation_sweep.get_config", side_effect=lambda k, d="": config.get(k, d)))
    stack.enter_context(patch("src.services.automation_sweep._has_installation", return_value=installed))
    if token is None:
        stack.enter_context(patch("src.services.automation_sweep.resolve_org_token", side_effect=NoGitHubTokenAvailable("none")))
    else:
        mocks["token"] = stack.enter_context(patch("src.services.automation_sweep.resolve_org_token", return_value=token))
    for target, value in extra_patches.items():
        mocks[target] = stack.enter_context(patch(value[0], **value[1]))
    return stack, mocks


def _actions(db, org):
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    return db.execute(
        text("SELECT action, actor FROM audit_logs WHERE tenant_id = :t ORDER BY id"), {"t": org.tenant_id}
    ).all()


def test_an_opted_in_org_is_nudged_and_triaged_with_installation_token_only(db, org):
    stack, m = _sweep()
    with stack:
        run_automation_sweep(db)
    # nudges only on the enabled repo, with the manual run's mode and stale days
    assert [(c.args[1], c.args[2]) for c in m["nudge"].call_args_list] == [("acme", "api")]
    assert m["nudge"].call_args.kwargs == {"stale_days": pr_nudge.configured_stale_days(), "mode": "comment"}
    # triage with the repo's saved mode and merge method, for real (not a dry run)
    assert m["triage"].call_args.kwargs["mode"] == "approve_and_merge"
    assert m["triage"].call_args.kwargs["merge_method"] == "rebase"
    assert m["triage"].call_args.kwargs["dry_run"] is False
    # an unattended run never accepts a caller-supplied token
    assert all(c.kwargs["client_token"] is None for c in m["token"].call_args_list)
    audited = _actions(db, org)
    assert ("pr_nudge.scheduled", "system:pr_nudge") in audited
    assert ("dependabot_triage.scheduled", "system:dependabot_triage") in audited
    assert ("pr_nudge.sweep", "system:pr_nudge") in audited


def test_a_second_tick_inside_the_cadence_does_nothing(db, org):
    stack, m = _sweep()
    with stack:
        run_automation_sweep(db)
        run_automation_sweep(db)
    assert m["nudge"].call_count == 1 and m["triage"].call_count == 1


def test_nothing_runs_when_the_org_follows_an_instance_that_is_off(db, org):
    org.pr_nudge_schedule = org.dependabot_triage_schedule = None
    db.commit()
    stack, m = _sweep(instance="off")
    with stack:
        run_automation_sweep(db)
    m["nudge"].assert_not_called()
    m["triage"].assert_not_called()


def test_the_instance_cadence_applies_unless_the_org_opts_out(db, org):
    org.pr_nudge_schedule = None  # follows the instance (weekly)
    org.dependabot_triage_schedule = False  # opted out
    db.commit()
    stack, m = _sweep(instance="weekly")
    with stack:
        run_automation_sweep(db)
    assert m["nudge"].call_count == 1
    m["triage"].assert_not_called()


def test_an_org_without_an_app_installation_or_token_is_skipped(db, org):
    stack, m = _sweep(installed=False)
    with stack:
        run_automation_sweep(db)
    m["nudge"].assert_not_called()

    stack, m = _sweep(token=None)
    with stack:
        run_automation_sweep(db)
    m["nudge"].assert_not_called()
    m["triage"].assert_not_called()


def test_a_repo_that_is_not_enabled_is_never_touched(db, org):
    automation_settings_repo.upsert(db, org.tenant_id, "acme/api", "pr_nudge", enabled=False)
    stack, m = _sweep()
    with stack:
        run_automation_sweep(db)
    m["nudge"].assert_not_called()  # no enabled repo left, so the org is not even a candidate


def test_nudge_mode_off_runs_nothing_on_github(db, org):
    stack, m = _sweep(mode="off")
    with stack:
        run_automation_sweep(db)
    m["nudge"].assert_not_called()
    assert ("pr_nudge.scheduled", "system:pr_nudge") in _actions(db, org)


def test_a_failure_is_recorded_and_not_retried_on_the_next_tick(db, org):
    stack, m = _sweep()
    m["nudge"].side_effect = RuntimeError("boom")  # not an httpx error: a bug, not a per-repo GitHub failure
    with stack:
        run_automation_sweep(db)
        run_automation_sweep(db)
    assert m["nudge"].call_count == 1
    assert ("pr_nudge.scheduled_failed", "system:pr_nudge") in _actions(db, org)
    assert ("pr_nudge.scheduled", "system:pr_nudge") not in _actions(db, org)


def test_triage_decisions_are_audited_under_the_system_actor(db, org):
    stack, m = _sweep()
    m["triage"].return_value = [dependabot_triage.Decision(7, "Bump x", "approved", "patch bump")]
    with stack:
        run_automation_sweep(db)
    assert ("dependabot_triage.approved", "system:dependabot_triage") in _actions(db, org)


def _user(db, email):
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, email_verified=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=None, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(schedules_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def test_org_controls_are_admin_only_audited_and_reflect_the_effective_schedule(db):
    org = org_repo.get_or_create(db, github_login="acme")
    admin, member = _user(db, "auto-admin@e.com"), _user(db, "auto-member@e.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    client = _client(db, admin)

    with patch("src.services.automation_sweep.get_config", return_value="off"):
        assert _client(db, member).get("/orgs/acme/automation-schedules").status_code == 403
        first = client.get("/orgs/acme/automation-schedules").json()
        assert first["pr_nudges"] == {"enabled": None, "effective": False, "cadence": None, "instance_cadence": "off"}
        on = client.put("/orgs/acme/automation-schedules", json={"pr_nudges": True}).json()
        repo = client.put("/orgs/acme/repos/acme/Api/automation/pr-nudge", json={"enabled": True})
        listed = client.get("/orgs/acme/automation-schedules").json()

    assert (on["pr_nudges"]["effective"], on["pr_nudges"]["cadence"]) == (True, "weekly")
    assert on["dependabot_triage"]["enabled"] is None  # only the sent field changed
    assert repo.status_code == 200
    assert listed["pr_nudge_repos"] == ["acme/api"]  # stored lower-cased (see #898)
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    actions = db.execute(
        text("SELECT action FROM audit_logs WHERE tenant_id = :t AND action IN ('pr_nudge_schedule.updated', 'pr_nudge.setting_saved')"),
        {"t": org.tenant_id},
    ).scalars().all()
    assert sorted(actions) == ["pr_nudge.setting_saved", "pr_nudge_schedule.updated"]
