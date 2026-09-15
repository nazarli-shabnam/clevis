"""Tests that gap_heal_loop wires src.services.sweep_loop.run_sweep_loop correctly (issue
#192/S5 PR 2). Loop/session/reset behavior itself is covered once, in test_sweep_loop.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services import gap_heal_loop
from src.services.gap_heal_sweep import run_gap_heal_sweep


@pytest.mark.asyncio
async def test_gap_heal_loop_wires_run_sweep_loop():
    with patch("src.services.gap_heal_loop.run_sweep_loop", new_callable=AsyncMock) as mock_run:
        await gap_heal_loop.gap_heal_loop()

    mock_run.assert_awaited_once_with(
        label="gap-heal",
        sweep_fn=run_gap_heal_sweep,
        config_key="gap_heal_poll_seconds",
        default_seconds=900,
        min_seconds=60,
        max_seconds=3600,
    )
