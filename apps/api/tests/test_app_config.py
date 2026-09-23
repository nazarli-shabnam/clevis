"""Tests for src.core.app_config.get_config's DB-read fallback behavior."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from src.core import app_config
from src.core.db import SessionLocal


@pytest.fixture(autouse=True)
def _reset_cache():
    app_config._cache.clear()
    yield
    app_config._cache.clear()


def test_get_config_reads_and_caches_db_value():
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO app_config (key, value, updated_at) VALUES "
                "('registration_enabled', 'false', NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            )
        )
        db.commit()
    try:
        assert app_config.get_config("registration_enabled", "true") == "false"
    finally:
        with SessionLocal() as db:
            db.execute(text("DELETE FROM app_config WHERE key = 'registration_enabled'"))
            db.commit()


def test_get_config_falls_back_to_default_on_read_failure_with_no_cache():
    with patch("src.core.db.SessionLocal", side_effect=RuntimeError("db down")):
        assert app_config.get_config("registration_enabled", "true") == "true"


def test_get_config_serves_last_known_good_value_on_read_failure():
    # A transient DB blip must keep serving the last good value, not flip a security setting
    # (e.g. registration_enabled) back to its code default.
    app_config._cache["registration_enabled"] = ("false", 0.0)  # timestamp 0 -> already stale
    with patch("src.core.db.SessionLocal", side_effect=RuntimeError("db down")):
        assert app_config.get_config("registration_enabled", "true") == "false"


def test_get_config_returns_fresh_cached_value_without_touching_db():
    app_config._cache["registration_enabled"] = ("false", __import__("time").monotonic())
    mock_session_factory = MagicMock()
    with patch("src.core.db.SessionLocal", mock_session_factory):
        assert app_config.get_config("registration_enabled", "true") == "false"
    mock_session_factory.assert_not_called()
