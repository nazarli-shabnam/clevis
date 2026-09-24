"""Behavioral tests for the shared sweep loop (src.services.sweep_loop); per-loop tests only check wiring."""

import asyncio
from unittest.mock import patch

import pytest

from src.services import sweep_loop


def test_read_poll_seconds_default():
    with patch("src.services.sweep_loop.get_config", return_value="900"):
        assert (
            sweep_loop._read_poll_seconds(config_key="k", default_seconds=900, min_seconds=60, max_seconds=3600) == 900
        )


def test_read_poll_seconds_clamps_high_values():
    with patch("src.services.sweep_loop.get_config", return_value="999999"):
        assert (
            sweep_loop._read_poll_seconds(config_key="k", default_seconds=900, min_seconds=60, max_seconds=3600) == 3600
        )


def test_read_poll_seconds_clamps_low_values():
    with patch("src.services.sweep_loop.get_config", return_value="1"):
        assert sweep_loop._read_poll_seconds(config_key="k", default_seconds=900, min_seconds=60, max_seconds=3600) == 60


def test_read_poll_seconds_falls_back_on_non_integer():
    with patch("src.services.sweep_loop.get_config", return_value="nope"):
        assert (
            sweep_loop._read_poll_seconds(config_key="k", default_seconds=900, min_seconds=60, max_seconds=3600) == 900
        )


class _FakeSession:
    def __init__(self):
        self.executed = []
        self.rolled_back = False
        self.committed = False

    def execute(self, stmt, params=None):
        self.executed.append(str(stmt))

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_run_sweep_resets_the_tenant_session_context_even_when_the_sweep_raises():
    # _run_sweep bypasses get_db()'s reset, so it must clear app.tenant_id or it leaks via the pool.
    fake_db = _FakeSession()
    failing_sweep = lambda db: (_ for _ in ()).throw(RuntimeError("boom"))  # noqa: E731

    with patch("src.services.sweep_loop.SessionLocal", return_value=fake_db), pytest.raises(RuntimeError):
        sweep_loop._run_sweep(failing_sweep)

    assert any("RESET app.tenant_id" in s for s in fake_db.executed)
    assert any("RESET app.user_id" in s for s in fake_db.executed)
    assert fake_db.committed is True


@pytest.mark.asyncio
async def test_loop_runs_the_sweep_then_sleeps_each_iteration():
    calls = {"sweep": 0, "sleep": 0}

    async def fake_to_thread(fn, *args):
        calls["sweep"] += 1

    async def fake_sleep(_seconds):
        calls["sleep"] += 1
        raise asyncio.CancelledError()

    with (
        patch("src.services.sweep_loop.asyncio.to_thread", side_effect=fake_to_thread),
        patch("src.services.sweep_loop.asyncio.sleep", side_effect=fake_sleep),
        patch("src.services.sweep_loop.get_config", return_value="900"),
        pytest.raises(asyncio.CancelledError),
    ):
        await sweep_loop.run_sweep_loop(
            label="test", sweep_fn=lambda db: None, config_key="k", default_seconds=900, min_seconds=60, max_seconds=3600
        )

    assert calls == {"sweep": 1, "sleep": 1}


@pytest.mark.asyncio
async def test_loop_survives_an_exception_from_the_sweep_and_still_sleeps():
    calls = {"sweep": 0, "sleep": 0}

    async def fake_to_thread(fn, *args):
        calls["sweep"] += 1
        raise RuntimeError("simulated sweep failure")

    async def fake_sleep(_seconds):
        calls["sleep"] += 1
        raise asyncio.CancelledError()

    with (
        patch("src.services.sweep_loop.asyncio.to_thread", side_effect=fake_to_thread),
        patch("src.services.sweep_loop.asyncio.sleep", side_effect=fake_sleep),
        patch("src.services.sweep_loop.get_config", return_value="900"),
        pytest.raises(asyncio.CancelledError),
    ):
        await sweep_loop.run_sweep_loop(
            label="test", sweep_fn=lambda db: None, config_key="k", default_seconds=900, min_seconds=60, max_seconds=3600
        )

    # The loop must still sleep and retry after the sweep raised, not die.
    assert calls == {"sweep": 1, "sleep": 1}
