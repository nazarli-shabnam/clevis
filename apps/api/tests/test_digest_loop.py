"""Tests that digest_loop wires src.services.sweep_loop.run_sweep_loop correctly (issue
#292). Loop/session/reset behavior itself is covered once, in test_sweep_loop.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services import digest_loop
from src.services.digest_sweep import run_digest_sweep


@pytest.mark.asyncio
async def test_digest_loop_wires_run_sweep_loop():
    with patch("src.services.digest_loop.run_sweep_loop", new_callable=AsyncMock) as mock_run:
        await digest_loop.digest_loop()

    mock_run.assert_awaited_once_with(
        label="leadership-digest",
        sweep_fn=run_digest_sweep,
        config_key="digest_poll_seconds",
        default_seconds=3_600,
        min_seconds=300,
        max_seconds=86_400,
    )
