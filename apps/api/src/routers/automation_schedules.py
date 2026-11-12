"""An org's controls for unattended automations: whether stale-PR nudges and Dependabot auto-triage run on a
schedule (overriding the instance cadence), and which repositories have stale-PR nudges enabled. The
scheduled runs themselves are services/automation_sweep.py."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.path_params import Owner, Repo
from src.core.rbac import OrgContext, assert_owner_matches_org, require_org_role
from src.repositories import audit_repo, automation_settings_repo
from src.services import automation_sweep

router = APIRouter()

PR_NUDGE_FEATURE = "pr_nudge"
TRIAGE_FEATURE = "dependabot_triage"
Cadence = Literal["daily", "weekly"]


class ScheduleSetting(BaseModel):
    enabled: bool | None  # the org's override; None = follows the instance
    effective: bool  # whether it runs on a schedule for this org
    cadence: Cadence | None
    instance_cadence: Literal["off", "daily", "weekly"]


class AutomationSchedules(BaseModel):
    pr_nudges: ScheduleSetting
    dependabot_triage: ScheduleSetting
    # Repositories the scheduled runs act on (admin-enabled per repo), lower-cased owner/name.
    pr_nudge_repos: list[str]
    triage_repos: list[str]


class AutomationSchedulesUpdate(BaseModel):
    """Only the fields sent change; null follows the instance setting again."""

    pr_nudges: bool | None = None
    dependabot_triage: bool | None = None


class PrNudgeRepoSetting(BaseModel):
    enabled: bool


_BY_FIELD = {
    "pr_nudges": ("pr_nudge", "pr_nudge_schedule"),
    "dependabot_triage": ("dependabot_triage", "dependabot_triage_schedule"),
}


def _setting(org, name: str) -> ScheduleSetting:
    auto = next(a for a in automation_sweep.AUTOMATIONS if a.name == name)
    override = getattr(org, auto.override_column)
    instance = automation_sweep.instance_cadence(auto.config_key)
    cadence = automation_sweep.effective_cadence(override, instance)
    return ScheduleSetting(enabled=override, effective=cadence is not None, cadence=cadence, instance_cadence=instance)


def _enabled_repo_names(db: Session, tenant_id: int, feature: str) -> list[str]:
    return sorted(s.repo for s in automation_settings_repo.list_for_feature(db, tenant_id, feature) if s.enabled)


def _snapshot(db: Session, org) -> AutomationSchedules:
    return AutomationSchedules(
        pr_nudges=_setting(org, "pr_nudge"),
        dependabot_triage=_setting(org, "dependabot_triage"),
        pr_nudge_repos=_enabled_repo_names(db, org.tenant_id, PR_NUDGE_FEATURE),
        triage_repos=_enabled_repo_names(db, org.tenant_id, TRIAGE_FEATURE),
    )


@router.get("/orgs/{org_login}/automation-schedules", response_model=AutomationSchedules)
def get_automation_schedules(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")), db: Session = Depends(get_db)
):
    return _snapshot(db, ctx.org)


@router.put("/orgs/{org_login}/automation-schedules", response_model=AutomationSchedules)
def set_automation_schedules(
    body: AutomationSchedulesUpdate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    org = ctx.org
    for field in body.model_fields_set:
        name, column = _BY_FIELD[field]
        new, previous = getattr(body, field), getattr(org, column)
        if new == previous:
            continue
        setattr(org, column, new)
        audit_repo.write(
            db, actor=user.email, action=f"{name}_schedule.updated", target=org.github_login,
            payload={"enabled": new, "previous": previous}, tenant_id=org.tenant_id, commit=False,
        )
    db.commit()
    return _snapshot(db, org)


@router.put("/orgs/{org_login}/repos/{owner}/{repo}/automation/pr-nudge", response_model=PrNudgeRepoSetting)
def set_pr_nudge_repo(
    owner: Owner,
    repo: Repo,
    body: PrNudgeRepoSetting,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Opt one repository in or out of scheduled stale-PR nudges (a manual nudge needs no opt-in)."""
    assert_owner_matches_org(owner, ctx)
    automation_settings_repo.upsert(db, ctx.org.tenant_id, f"{owner}/{repo}", PR_NUDGE_FEATURE, enabled=body.enabled)
    audit_repo.write(
        db, actor=user.email, action="pr_nudge.setting_saved", target=f"{owner}/{repo}",
        payload={"enabled": body.enabled}, tenant_id=ctx.org.tenant_id,
    )
    return body
