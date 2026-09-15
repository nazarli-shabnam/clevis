"""Tests that membership_reconcile_loop wires src.services.sweep_loop.run_sweep_loop
correctly (Collaborators PR 2 of 3). Loop/session/reset behavior itself is covered once,
in test_sweep_loop.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services import membership_reconcile_loop
from src.services.membership_reconcile_sweep import run_membership_reconcile_sweep


@pytest.mark.asyncio
async def test_membership_reconcile_loop_wires_run_sweep_loop():
    with patch("src.services.membership_reconcile_loop.run_sweep_loop", new_callable=AsyncMock) as mock_run:
        await membership_reconcile_loop.membership_reconcile_loop()

    mock_run.assert_awaited_once_with(
        label="membership-reconcile",
        sweep_fn=run_membership_reconcile_sweep,
        config_key="membership_reconcile_poll_seconds",
        default_seconds=900,
        min_seconds=60,
        max_seconds=3600,
    )
