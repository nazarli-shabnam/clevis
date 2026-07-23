from unittest.mock import patch

from worker import _MAX_POLL_SECONDS, _read_poll_seconds


def test_read_poll_seconds_clamps_to_max():
    # worker_poll_seconds is a live-editable app_config value -- an operator setting it
    # above _MAX_POLL_SECONDS must not silently make the heartbeat healthcheck's staleness
    # threshold trip on every normal iteration (see the comment on _MAX_POLL_SECONDS).
    with patch("worker._read_app_config", return_value="600"):
        assert _read_poll_seconds() == _MAX_POLL_SECONDS


def test_read_poll_seconds_clamps_to_min():
    with patch("worker._read_app_config", return_value="0"):
        assert _read_poll_seconds() == 1


def test_read_poll_seconds_passes_through_a_value_within_range():
    with patch("worker._read_app_config", return_value="10"):
        assert _read_poll_seconds() == 10


def test_read_poll_seconds_falls_back_to_5_on_malformed_value():
    with patch("worker._read_app_config", return_value="not-a-number"):
        assert _read_poll_seconds() == 5
