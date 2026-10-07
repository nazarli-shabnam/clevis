"""The worker's startup notice that Row-Level Security does not apply to its database role (#455)."""

import logging
from unittest.mock import MagicMock, patch

import psycopg
import worker


class _StopLoop(Exception):
    pass


def _patched_connect(row=None, error=None):
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    connect = MagicMock(side_effect=error) if error else MagicMock(return_value=conn)
    return patch.object(worker.psycopg, "connect", connect)


def test_worker_warns_once_when_its_role_bypasses_rls(caplog):
    with caplog.at_level(logging.WARNING, logger="worker"), _patched_connect(row=("clevis", True, False)):
        assert worker._warn_if_rls_bypassed() is True
    assert "NOT enforced for the worker" in caplog.text and "WORKER_DB_PASSWORD" in caplog.text


def test_worker_is_silent_for_an_ordinary_role_or_when_the_check_fails(caplog):
    with caplog.at_level(logging.WARNING, logger="worker"):
        with _patched_connect(row=("clevis_worker", False, False)):
            assert worker._warn_if_rls_bypassed() is False
        with _patched_connect(row=None):
            assert worker._warn_if_rls_bypassed() is False
        with _patched_connect(error=psycopg.OperationalError("down")):
            assert worker._warn_if_rls_bypassed() is False
    assert "NOT enforced" not in caplog.text


def test_run_checks_the_rls_posture_before_polling(monkeypatch):
    warned = MagicMock()
    monkeypatch.setattr(worker, "_warn_if_rls_bypassed", warned)
    monkeypatch.setattr(worker, "_read_poll_seconds", lambda: 1)
    monkeypatch.setattr(worker.time, "sleep", MagicMock(side_effect=_StopLoop))
    try:
        worker.run()
    except _StopLoop:
        pass
    warned.assert_called_once_with()
