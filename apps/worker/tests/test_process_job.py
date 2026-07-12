"""Worker job processing: unit-style tests with mocked GitHub HTTP and psycopg connection."""

import json
from unittest.mock import MagicMock, patch

from _crypto import encrypt_job_token
from config import settings
from worker import process_job


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeConn:
    def __init__(self):
        self.committed = False
        self._cursor = _FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def _payload(**kwargs):
    enc = encrypt_job_token("secret", settings.job_secret_key.get_secret_value())
    return json.dumps({"owner": "acme", "repo": "demo", "token": enc, **kwargs})


def test_process_job_marks_done_on_success():
    conn = _FakeConn()

    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_response.text = ""

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        process_job(conn, 1, "github.clear_actions_cache", _payload())

    sql, params = conn._cursor.calls[0]
    assert "status='done'" in sql
    assert params[1] == 1
    result = json.loads(params[0])
    assert result["ok"] is True
    assert result["status"] == 204
    assert conn.committed is True


def test_process_job_marks_failed_on_http_error():
    conn = _FakeConn()

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "upstream error"

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        process_job(conn, 2, "github.clear_actions_cache", _payload())

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert params[1] == 2
    assert "GitHub API error" in params[0]
    assert conn.committed is True


def test_process_job_marks_failed_on_network_error():
    conn = _FakeConn()

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(side_effect=ConnectionError("timeout"))
        mock_client_cls.return_value = mock_client

        process_job(conn, 3, "github.clear_actions_cache", _payload())

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert "timeout" in params[0]
    assert conn.committed is True


def test_process_job_truncates_long_error():
    conn = _FakeConn()

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(side_effect=ConnectionError("x" * 1000))
        mock_client_cls.return_value = mock_client

        process_job(conn, 4, "github.clear_actions_cache", _payload())

    sql, params = conn._cursor.calls[0]
    assert len(params[0]) <= 500
    assert params[0].endswith("...(truncated)")


def test_process_job_redacts_token_shaped_text_in_error():
    conn = _FakeConn()

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(
            side_effect=ConnectionError("failed with ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        )
        mock_client_cls.return_value = mock_client

        process_job(conn, 5, "github.clear_actions_cache", _payload())

    sql, params = conn._cursor.calls[0]
    assert "ghp_" not in params[0]
    assert "[redacted]" in params[0]


def test_process_job_marks_failed_on_unknown_job_type():
    conn = _FakeConn()
    process_job(conn, 9, "github.unknown_job", _payload())
    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert "Unsupported job_type" in params[0]
    assert conn.committed is True
