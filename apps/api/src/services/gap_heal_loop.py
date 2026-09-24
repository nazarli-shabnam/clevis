"""Periodic gap-heal sweep, wired via sweep_loop.run_sweep_loop. Started from
src.main's lifespan. See sweep_loop.py for the shared loop/session/reset shape.
"""

from src.services.gap_heal_sweep import run_gap_heal_sweep
from src.services.sweep_loop import run_sweep_loop

# An upper clamp so a misconfigured app_config value can't make the loop effectively never run.
_MAX_POLL_SECONDS = 3600
_MIN_POLL_SECONDS = 60
_DEFAULT_POLL_SECONDS = 900


async def gap_heal_loop() -> None:
    await run_sweep_loop(
        label="gap-heal",
        sweep_fn=run_gap_heal_sweep,
        config_key="gap_heal_poll_seconds",
        default_seconds=_DEFAULT_POLL_SECONDS,
        min_seconds=_MIN_POLL_SECONDS,
        max_seconds=_MAX_POLL_SECONDS,
    )
