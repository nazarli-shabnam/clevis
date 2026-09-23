"""Periodic org-membership reconciliation sweep, wired via sweep_loop.run_sweep_loop.
Started from src.main's lifespan as an independent task from gap_heal_loop/digest_loop --
the sweeps are unrelated and each already tolerates a single iteration's exception
without dying. See sweep_loop.py for the shared loop/session/reset shape.
"""

from src.services.membership_reconcile_sweep import run_membership_reconcile_sweep
from src.services.sweep_loop import run_sweep_loop

# Same reasoning as gap_heal_loop.py's own clamp -- an upper bound so a misconfigured
# app_config value can't make the loop effectively never run.
_MAX_POLL_SECONDS = 3600
_MIN_POLL_SECONDS = 60
_DEFAULT_POLL_SECONDS = 900


async def membership_reconcile_loop() -> None:
    await run_sweep_loop(
        label="membership-reconcile",
        sweep_fn=run_membership_reconcile_sweep,
        config_key="membership_reconcile_poll_seconds",
        default_seconds=_DEFAULT_POLL_SECONDS,
        min_seconds=_MIN_POLL_SECONDS,
        max_seconds=_MAX_POLL_SECONDS,
    )
