"""Periodic unattended-automation sweep (stale-PR nudges, Dependabot auto-triage), wired via
sweep_loop.run_sweep_loop. Started from src.main's lifespan. A no-op unless ``pr_nudge_cadence`` or
``dependabot_triage_cadence`` is daily/weekly, or an org opted in itself. See automation_sweep.py.
"""

from src.services.automation_sweep import run_automation_sweep
from src.services.sweep_loop import run_sweep_loop

_MAX_POLL_SECONDS = 86_400
_MIN_POLL_SECONDS = 300
_DEFAULT_POLL_SECONDS = 3_600


async def automation_loop() -> None:
    await run_sweep_loop(
        label="automation",
        sweep_fn=run_automation_sweep,
        config_key="automation_poll_seconds",
        default_seconds=_DEFAULT_POLL_SECONDS,
        min_seconds=_MIN_POLL_SECONDS,
        max_seconds=_MAX_POLL_SECONDS,
    )
