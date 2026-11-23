"""Wiring of the API's background loops (#1256): each thin wrapper hands run_sweep_loop the right sweep, label,
config key and poll bounds, and the lifespan starts every one of them. run_sweep_loop itself is covered by
test_sweep_loop.py; a wrong import or a mistyped key in a wrapper would otherwise fail silently (e.g. retention
would simply stop purging)."""

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from src.main import lifespan

# (loop module, loop function, sweep module, sweep function, label, config key, default, min, max)
LOOPS = [
    ("gap_heal_loop", "gap_heal_loop", "gap_heal_sweep", "run_gap_heal_sweep", "gap-heal", "gap_heal_poll_seconds", 900, 60, 3600),
    (
        "membership_reconcile_loop", "membership_reconcile_loop", "membership_reconcile_sweep",
        "run_membership_reconcile_sweep", "membership-reconcile", "membership_reconcile_poll_seconds", 900, 60, 3600,
    ),
    ("digest_loop", "digest_loop", "digest_sweep", "run_digest_sweep", "leadership-digest", "digest_poll_seconds", 3600, 300, 86400),
    (
        "scheduled_scan_loop", "scheduled_scan_loop", "scheduled_scan_sweep", "run_scheduled_scan_sweep",
        "scheduled-scan", "scheduled_scan_poll_seconds", 3600, 300, 86400,
    ),
    ("automation_loop", "automation_loop", "automation_sweep", "run_automation_sweep", "automation", "automation_poll_seconds", 3600, 300, 86400),
    (
        "webhook_requeue_loop", "webhook_requeue_loop", "webhook_requeue_sweep", "run_webhook_requeue_sweep",
        "webhook-requeue", "webhook_requeue_poll_seconds", 300, 60, 3600,
    ),
    (
        "waiver_expiry_loop", "waiver_expiry_loop", "waiver_expiry_sweep", "run_waiver_expiry_sweep",
        "waiver-expiry", "waiver_expiry_poll_seconds", 3600, 3600, 3600,
    ),
    # Fixed cadences: the poll key is a placeholder (not an app_config key), min = max = default.
    ("retention_loop", "retention_loop", "retention_sweep", "run_retention_sweep", "retention", "retention_poll_seconds", 21600, 21600, 21600),
    (
        "security_alert_loop", "security_alert_loop", "security_alert_sweep", "run_security_alert_sweep",
        "security-alert", "security_alert_poll_seconds", 300, 300, 300,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("loop_module,loop_fn,sweep_module,sweep_fn,label,config_key,default,low,high", LOOPS)
async def test_each_loop_passes_its_sweep_label_and_config_to_run_sweep_loop(
    loop_module, loop_fn, sweep_module, sweep_fn, label, config_key, default, low, high
):
    module = importlib.import_module(f"src.services.{loop_module}")
    sweep = getattr(importlib.import_module(f"src.services.{sweep_module}"), sweep_fn)
    with patch.object(module, "run_sweep_loop", new=AsyncMock()) as run:
        await getattr(module, loop_fn)()
    run.assert_awaited_once_with(
        label=label, sweep_fn=sweep, config_key=config_key, default_seconds=default, min_seconds=low, max_seconds=high
    )


@pytest.mark.asyncio
async def test_lifespan_starts_every_background_loop():
    names = [row[1] for row in LOOPS]

    async def forever():
        await asyncio.sleep(3600)

    mocks = {name: MagicMock(side_effect=forever) for name in names}
    patches = [patch(f"src.main.{name}", mock) for name, mock in mocks.items()]
    for p in patches:
        p.start()
    try:
        async with lifespan(FastAPI()):
            pass
    finally:
        for p in patches:
            p.stop()
    assert {name: mock.call_count for name, mock in mocks.items()} == dict.fromkeys(names, 1)
