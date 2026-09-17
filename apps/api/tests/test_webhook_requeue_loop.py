"""Tests that webhook_requeue_loop wires src.services.sweep_loop.run_sweep_loop correctly
(issue #409). Loop/session/reset behavior itself is covered once, in test_sweep_loop.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services import webhook_requeue_loop
from src.services.webhook_requeue_sweep import run_webhook_requeue_sweep


@pytest.mark.asyncio
async def test_webhook_requeue_loop_wires_run_sweep_loop():
    with patch("src.services.webhook_requeue_loop.run_sweep_loop", new_callable=AsyncMock) as mock_run:
        await webhook_requeue_loop.webhook_requeue_loop()

    mock_run.assert_awaited_once_with(
        label="webhook-requeue",
        sweep_fn=run_webhook_requeue_sweep,
        config_key="webhook_requeue_poll_seconds",
        default_seconds=300,
        min_seconds=60,
        max_seconds=3600,
    )
