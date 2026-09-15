"""Issue #292: periodic leadership-digest sweep, wired via sweep_loop.run_sweep_loop.
Started from src.main's lifespan. The sweep itself is a no-op unless the `digest_cadence`
instance-config key is set to weekly or monthly. See sweep_loop.py for the shared
loop/session/reset shape.
"""

from src.services.digest_sweep import run_digest_sweep
from src.services.sweep_loop import run_sweep_loop

_MAX_POLL_SECONDS = 86_400
_MIN_POLL_SECONDS = 300
_DEFAULT_POLL_SECONDS = 3_600


async def digest_loop() -> None:
    await run_sweep_loop(
        label="leadership-digest",
        sweep_fn=run_digest_sweep,
        config_key="digest_poll_seconds",
        default_seconds=_DEFAULT_POLL_SECONDS,
        min_seconds=_MIN_POLL_SECONDS,
        max_seconds=_MAX_POLL_SECONDS,
    )
