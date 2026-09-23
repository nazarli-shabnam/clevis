"""Periodic webhook_deliveries re-enqueue sweep, wired via sweep_loop.run_sweep_loop.
Started from src.main's lifespan. See sweep_loop.py for the shared loop/session/reset shape.
"""

from src.services.sweep_loop import run_sweep_loop
from src.services.webhook_requeue_sweep import run_webhook_requeue_sweep

# A stuck webhook is more time-sensitive than gap-healing (it's already fully durably
# stored -- this is just retrying the last step), so it ticks more often by default, but
# still on the same [60, 3600]-second clamp shape as the other sweeps.
_MAX_POLL_SECONDS = 3600
_MIN_POLL_SECONDS = 60
_DEFAULT_POLL_SECONDS = 300


async def webhook_requeue_loop() -> None:
    await run_sweep_loop(
        label="webhook-requeue",
        sweep_fn=run_webhook_requeue_sweep,
        config_key="webhook_requeue_poll_seconds",
        default_seconds=_DEFAULT_POLL_SECONDS,
        min_seconds=_MIN_POLL_SECONDS,
        max_seconds=_MAX_POLL_SECONDS,
    )
