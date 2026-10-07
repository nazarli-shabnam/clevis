"""Periodic scheduled-scan sweep, wired via sweep_loop.run_sweep_loop. Started from src.main's
lifespan. A no-op unless the `scheduled_scan_cadence` instance-config key is daily or weekly, or an org
has opted in itself. See sweep_loop.py for the shared loop/session/reset shape.
"""

from src.services.scheduled_scan_sweep import run_scheduled_scan_sweep
from src.services.sweep_loop import run_sweep_loop

_MAX_POLL_SECONDS = 86_400
_MIN_POLL_SECONDS = 300
_DEFAULT_POLL_SECONDS = 3_600


async def scheduled_scan_loop() -> None:
    await run_sweep_loop(
        label="scheduled-scan",
        sweep_fn=run_scheduled_scan_sweep,
        config_key="scheduled_scan_poll_seconds",
        default_seconds=_DEFAULT_POLL_SECONDS,
        min_seconds=_MIN_POLL_SECONDS,
        max_seconds=_MAX_POLL_SECONDS,
    )
