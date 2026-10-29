"""Periodic waiver-expiry sweep, wired via sweep_loop.run_sweep_loop. Started from src.main's lifespan.
Fixed hourly cadence (not a config key): an expiry is announced within the hour, which is all a day-scale
exception needs."""

from src.services.sweep_loop import run_sweep_loop
from src.services.waiver_expiry_sweep import run_waiver_expiry_sweep

_POLL_SECONDS = 3_600


async def waiver_expiry_loop() -> None:
    await run_sweep_loop(
        label="waiver-expiry",
        sweep_fn=run_waiver_expiry_sweep,
        config_key="waiver_expiry_poll_seconds",
        default_seconds=_POLL_SECONDS,
        min_seconds=_POLL_SECONDS,
        max_seconds=_POLL_SECONDS,
    )
