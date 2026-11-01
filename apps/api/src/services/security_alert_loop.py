"""Periodic sweep announcing newly ingested critical/high security alerts, wired via sweep_loop.run_sweep_loop.
Started from src.main's lifespan. Fixed 5-minute cadence (not a config key): an alert reaches chat within minutes."""

from src.services.security_alert_sweep import run_security_alert_sweep
from src.services.sweep_loop import run_sweep_loop

_POLL_SECONDS = 300


async def security_alert_loop() -> None:
    await run_sweep_loop(
        label="security-alert",
        sweep_fn=run_security_alert_sweep,
        config_key="security_alert_poll_seconds",
        default_seconds=_POLL_SECONDS,
        min_seconds=_POLL_SECONDS,
        max_seconds=_POLL_SECONDS,
    )
