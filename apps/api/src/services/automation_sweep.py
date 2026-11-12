"""Per-tick work for the unattended-automation loop: stale-PR nudges and Dependabot auto-triage.

Both are opt-in per repo (``automation_repo_settings``) and were manual-only; this runs them on a cadence.
It follows scheduled_scan_sweep.py and keeps its safety rails, because it writes to GitHub with nobody watching:
- installation tokens only (``client_token=None``): an org without a GitHub App is never touched, and a pasted
  token is never used for an unattended run;
- only repos an admin enabled for that feature, at most MAX_REPOS_PER_RUN per org, and at most
  MAX_ORGS_PER_TICK orgs per automation per tick, longest-waiting first;
- a per-tenant advisory lock plus a re-check of "is it still due" after taking it, so two API replicas don't
  both run the same org;
- a ``<name>.scheduled_started`` marker is committed before the GitHub work, a failure is recorded as
  ``<name>.scheduled_failed`` and retried only after FAILURE_RETRY, and success as ``<name>.scheduled``;
- every write to a PR is audited under the system actor, exactly like the manual run;
- a run in which any repository or PR errored is recorded as failed (retried after FAILURE_RETRY), not as a
  success that would hold the rest back for a whole cadence; a missing/unmintable token is recorded the same
  way, so such orgs cannot occupy every per-tick slot;
- when an org enables more than MAX_REPOS_PER_RUN repos, each run takes the next window of them.
The nudge mode (``pr_nudge_mode``) and stale-days settings apply as for a manual nudge. Mode ``off`` runs
nothing and records nothing, so the first run after it is turned on is not held back by a skipped one.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.db import set_session_tenant
from src.repositories import audit_repo, automation_settings_repo
from src.services import dependabot_triage, notifications, pr_nudge
from src.services.github_client import GitHubClient
from src.services.scan_schedule import CADENCE_INTERVAL
from src.services.sweep_lock import try_acquire_sweep_slot
from src.services.token_resolution import resolve_org_token

logger = logging.getLogger(__name__)

MAX_ORGS_PER_TICK = 3
MAX_REPOS_PER_RUN = 20
_GRACE = timedelta(hours=1)
FAILURE_RETRY = timedelta(hours=6)
IN_PROGRESS_WINDOW = timedelta(hours=1)
# An org that opts in while the instance cadence is off runs weekly, the gentlest setting.
_OPT_IN_DEFAULT = "weekly"


def instance_cadence(config_key: str) -> str:
    raw = (get_config(config_key, "off") or "off").strip().lower()
    return raw if raw in CADENCE_INTERVAL else "off"


def effective_cadence(override: bool | None, instance: str) -> str | None:
    """True = on, False = off, NULL = follow the instance; an opt-in with the instance off runs weekly."""
    if override is False:
        return None
    if instance in CADENCE_INTERVAL:
        return instance
    return _OPT_IN_DEFAULT if override is True else None


def _repo_parts(full_repo: str) -> tuple[str, str]:
    owner, _, name = full_repo.partition("/")
    return owner, name


def _run_pr_nudges(db: Session, client: GitHubClient, tenant_id: int, org_login: str, repos: list, actor: str) -> dict:
    mode = get_config(pr_nudge.MODE_KEY, pr_nudge.DEFAULT_MODE)
    stale_days = pr_nudge.configured_stale_days()
    nudged_total, failed = 0, 0
    for setting in repos:
        owner, name = _repo_parts(setting.repo)
        try:
            results = pr_nudge.run_nudge_sweep(client, owner, name, stale_days=stale_days, mode=mode)
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            failed += 1
            audit_repo.write(
                db, actor, "pr_nudge.sweep", setting.repo,
                {"mode": mode, "stale_days": stale_days, "error": type(exc).__name__}, tenant_id=tenant_id,
            )
            continue
        acted = [asdict(r) for r in results if r.action in ("commented", "labeled", "error")]
        if any(r["action"] == "error" for r in acted):
            failed += 1
        audit_repo.write(
            db, actor, "pr_nudge.sweep", setting.repo,
            {"mode": mode, "stale_days": stale_days, "results": acted}, tenant_id=tenant_id,
        )
        nudged = [r for r in acted if r["action"] in ("commented", "labeled")]
        nudged_total += len(nudged)
        try:
            notifications.notify_stale_prs(db, tenant_id, owner, name, nudged, actor=actor)
        except Exception:
            db.rollback()
            logger.exception("stale PR summary alert failed for %s", setting.repo)
    if failed:
        raise RuntimeError(f"{failed} of {len(repos)} repositories failed")
    return {"repos": len(repos), "nudged": nudged_total, "failed": failed}


def _run_dependabot_triage(
    db: Session, client: GitHubClient, tenant_id: int, org_login: str, repos: list, actor: str
) -> dict:
    acted, failed = 0, 0
    for setting in repos:
        owner, name = _repo_parts(setting.repo)
        try:
            decisions = dependabot_triage.triage(
                client, owner, name, enabled=True,
                mode=setting.mode or dependabot_triage.MODE_APPROVE_ONLY,
                merge_method=(setting.extra or {}).get("merge_method", "squash"),
                dry_run=False,
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            failed += 1
            audit_repo.write(
                db, actor, "dependabot_triage.error", setting.repo, {"reason": type(exc).__name__}, tenant_id=tenant_id
            )
            continue
        if any(d.action in ("error", "merge_failed") for d in decisions):
            failed += 1
        for d in decisions:
            acted += d.action in ("approved", "merged")
            # audit_repo.write commits per call, so a decision already acted on GitHub is never left unaudited.
            audit_repo.write(
                db, actor, f"dependabot_triage.{d.action}",
                f"{setting.repo}#{d.number}" if d.number else setting.repo, {"reason": d.reason}, tenant_id=tenant_id,
            )
    if failed:
        raise RuntimeError(f"{failed} of {len(repos)} repositories failed")
    return {"repos": len(repos), "acted": acted, "failed": failed}


@dataclass(frozen=True)
class Automation:
    name: str  # audit-action prefix and advisory-lock key
    config_key: str  # instance cadence (app_config)
    override_column: str  # orgs column; a constant, never user input
    feature: str  # automation_repo_settings.feature
    run: Callable[[Session, GitHubClient, int, str, list, str], dict]
    # False while the automation is switched off instance-wide: nothing is selected, run or recorded.
    available: Callable[[], bool] = lambda: True

    @property
    def actor(self) -> str:
        return f"system:{self.name}"


def _pr_nudge_available() -> bool:
    return get_config(pr_nudge.MODE_KEY, pr_nudge.DEFAULT_MODE) in pr_nudge.MODES and get_config(pr_nudge.MODE_KEY, pr_nudge.DEFAULT_MODE) != "off"


AUTOMATIONS = (
    Automation("pr_nudge", "pr_nudge_cadence", "pr_nudge_schedule", "pr_nudge", _run_pr_nudges, _pr_nudge_available),
    Automation(
        "dependabot_triage", "dependabot_triage_cadence", "dependabot_triage_schedule", "dependabot_triage",
        _run_dependabot_triage,
    ),
)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _last_action_at(db: Session, tenant_id: int, action: str) -> datetime | None:
    row = db.execute(
        text("SELECT MAX(created_at) FROM audit_logs WHERE tenant_id = :t AND action = :a"),
        {"t": tenant_id, "a": action},
    ).fetchone()
    return _aware(row[0]) if row else None


def _due_since(db: Session, auto: Automation, tenant_id: int, cadence: str, now: datetime) -> datetime | None:
    """None when the org is not due, else when it last ran (the epoch if never), to run the longest-waiting first."""
    last_run = _last_action_at(db, tenant_id, f"{auto.name}.scheduled")
    if last_run is not None and now - last_run < CADENCE_INTERVAL[cadence] - _GRACE:
        return None
    failed = _last_action_at(db, tenant_id, f"{auto.name}.scheduled_failed")
    if failed is not None and now - failed < FAILURE_RETRY:
        return None
    started = _last_action_at(db, tenant_id, f"{auto.name}.scheduled_started")
    if started is not None and now - started < IN_PROGRESS_WINDOW and (last_run is None or started > last_run):
        return None  # another replica (or an earlier tick) is running it right now
    return last_run or datetime.fromtimestamp(0, tz=timezone.utc)


def _has_installation(db: Session, org_id: int) -> bool:
    return (
        db.execute(
            text("SELECT 1 FROM github_installations WHERE org_id = :o AND installation_id IS NOT NULL LIMIT 1"),
            {"o": org_id},
        ).first()
        is not None
    )


def _enabled_repos(db: Session, tenant_id: int, feature: str, run_index: int = 0) -> list:
    """The enabled repos for one run: all of them, or, past MAX_REPOS_PER_RUN, the window ``run_index`` selects."""
    rows = [s for s in automation_settings_repo.list_for_feature(db, tenant_id, feature) if s.enabled and "/" in s.repo]
    if len(rows) > MAX_REPOS_PER_RUN:
        start = (run_index * MAX_REPOS_PER_RUN) % len(rows)
        rows = rows[start:] + rows[:start]
    return rows[:MAX_REPOS_PER_RUN]


def _record_failure(db: Session, auto: Automation, tenant_id: int, org_login: str, exc: Exception) -> bool:
    db.rollback()
    logger.exception("scheduled %s failed for %s", auto.name, org_login, exc_info=exc)
    set_session_tenant(db, tenant_id)
    audit_repo.write(
        db, actor=auto.actor, action=f"{auto.name}.scheduled_failed", target=org_login,
        payload={"error": type(exc).__name__}, tenant_id=tenant_id,
    )
    return False


def _run_one(db: Session, auto: Automation, org_id: int, tenant_id: int, org_login: str, cadence: str, now: datetime) -> bool:
    set_session_tenant(db, tenant_id)
    if not try_acquire_sweep_slot(db, auto.name, tenant_id):
        return False
    if _due_since(db, auto, tenant_id, cadence, now) is None:  # re-check under the lock
        db.commit()
        return False
    try:
        token = resolve_org_token(db, org_id=org_id, account_login=org_login, client_token=None)
    except Exception as exc:
        # Includes NoGitHubTokenAvailable: record it so FAILURE_RETRY moves this org out of the candidates and
        # the next ones get their slot, instead of the same unmintable orgs being picked every tick.
        return _record_failure(db, auto, tenant_id, org_login, exc)
    # One window per cadence period, so a run after the previous one starts at the next repos.
    run_index = int(now.timestamp() // CADENCE_INTERVAL[cadence].total_seconds())
    repos = _enabled_repos(db, tenant_id, auto.feature, run_index)
    if not repos:
        db.commit()
        return False
    # Commit the marker (releasing the transaction-scoped lock) before the long GitHub work.
    audit_repo.write(db, actor=auto.actor, action=f"{auto.name}.scheduled_started", target=org_login, payload={}, tenant_id=tenant_id)
    try:
        summary = auto.run(db, GitHubClient(token), tenant_id, org_login, repos, auto.actor)
        audit_repo.write(
            db, actor=auto.actor, action=f"{auto.name}.scheduled", target=org_login,
            payload={"cadence": cadence, **summary}, tenant_id=tenant_id,
        )
    except Exception as exc:
        return _record_failure(db, auto, tenant_id, org_login, exc)
    return True


def run_automation_sweep(db: Session) -> None:
    now = datetime.now(timezone.utc)
    for auto in AUTOMATIONS:
        if not auto.available():
            continue
        instance = instance_cadence(auto.config_key)
        # tenants has no RLS (identity table) -- read freely, then scope per tenant below.
        rows = db.execute(
            text(
                f"SELECT t.id, o.id, o.github_login, o.{auto.override_column} FROM tenants t "  # noqa: S608 (constant column)
                "JOIN orgs o ON o.id = t.org_id WHERE t.kind = 'org'"
            )
        ).fetchall()
        candidates: list[tuple[datetime, int, int, str, str]] = []
        for tenant_id, org_id, org_login, override in rows:
            cadence = effective_cadence(override, instance)
            if cadence is None:
                continue
            try:
                # github_installations and automation_repo_settings are tenant-isolated: read with the tenant set.
                set_session_tenant(db, tenant_id)
                if not _has_installation(db, org_id) or not _enabled_repos(db, tenant_id, auto.feature):
                    continue
                since = _due_since(db, auto, tenant_id, cadence, now)
            except Exception:
                db.rollback()
                logger.exception("scheduled %s: due check failed for tenant %d", auto.name, tenant_id)
                continue
            if since is not None:
                candidates.append((since, tenant_id, org_id, org_login, cadence))
        ordered = sorted(candidates)
        for _, tenant_id, org_id, org_login, cadence in ordered[:MAX_ORGS_PER_TICK]:
            try:
                _run_one(db, auto, org_id, tenant_id, org_login, cadence, now)
            except Exception:
                db.rollback()
                logger.exception("scheduled %s: iteration failed for tenant %d", auto.name, tenant_id)
        if len(ordered) > MAX_ORGS_PER_TICK:
            logger.info("scheduled %s: per-tick cap of %d reached; %d org(s) still due", auto.name, MAX_ORGS_PER_TICK, len(ordered) - MAX_ORGS_PER_TICK)
