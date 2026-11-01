"""Periodic retention purge, wired via sweep_loop.run_sweep_loop. Started from src.main's lifespan.
Fixed six-hourly cadence (not a config key): retention is measured in days, so the exact tick does not
matter, and a backlog larger than one tick's cap simply drains over the next few."""

from src.services.retention_sweep import run_retention_sweep
from src.services.sweep_loop import run_sweep_loop

_POLL_SECONDS = 21_600


async def retention_loop() -> None:
    await run_sweep_loop(
        label="retention",
        sweep_fn=run_retention_sweep,
        config_key="retention_poll_seconds",
        default_seconds=_POLL_SECONDS,
        min_seconds=_POLL_SECONDS,
        max_seconds=_POLL_SECONDS,
    )
