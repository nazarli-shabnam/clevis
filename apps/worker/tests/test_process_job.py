"""Worker job processing: unit-style tests with mocked GitHub HTTP and psycopg connection."""

import json
from unittest.mock import MagicMock, patch

import httpx

from _crypto import encrypt_job_token
from config import settings
from worker import MAX_RETRIES, process_job


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

        process_job(conn, 1, _payload())

    sql, params = conn._cursor.calls[0]
    assert "status='done'" in sql
    assert params[1] == 1
    result = json.loads(params[0])
    assert result["ok"] is True
    assert result["status"] == 204
    assert conn.committed is True


def test_process_job_marks_failed_on_4xx_http_error():
    """4xx is a permanent failure (bad token, missing repo, etc.) — never worth retrying."""
    conn = _FakeConn()

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "not found"

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        process_job(conn, 2, _payload())

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert params[1] == 2
    assert "GitHub API error" in params[0]
    assert conn.committed is True


def test_process_job_requeues_on_5xx_http_error():
    """5xx is presumed transient (GitHub-side issue) — worth a bounded retry, unlike 4xx."""
    conn = _FakeConn()

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "upstream error"

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        process_job(conn, 6, _payload(), retry_count=0)

    sql, params = conn._cursor.calls[0]
    assert "status='queued'" in sql
    new_retry_count, result_text, job_id = params
    assert new_retry_count == 1
    assert job_id == 6
    assert "GitHub API error" in result_text
    assert conn.committed is True


def test_process_job_requeues_on_httpx_request_error():
    """A genuine httpx.RequestError (what httpx actually raises for network failures) is
    treated as transient and requeued, distinct from an unrecognized exception type."""
    conn = _FakeConn()

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value = mock_client

        process_job(conn, 7, _payload(), retry_count=2)

    sql, params = conn._cursor.calls[0]
    assert "status='queued'" in sql
    new_retry_count, result_text, job_id = params
    assert new_retry_count == 3  # incremented from the passed-in retry_count=2
    assert job_id == 7
    assert "connection refused" in result_text
    assert conn.committed is True


def test_process_job_fails_once_retry_cap_exceeded():
    conn = _FakeConn()

    mock_response = MagicMock()
    mock_response.status_code = 503

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        process_job(conn, 8, _payload(), retry_count=MAX_RETRIES)

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    new_retry_count, result_text, job_id = params
    assert new_retry_count == MAX_RETRIES + 1
    assert job_id == 8
    assert "exceeded max retry attempts" in result_text
    assert conn.committed is True


def test_process_job_marks_failed_on_unrecognized_exception_safety_net():
    """Something other than httpx.RequestError (a bug, an unanticipated exception type)
    must still fail the job immediately rather than leaving it stuck in 'processing'
    until the reclaim sweep eventually picks it up."""
    conn = _FakeConn()

    with patch("worker.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete = MagicMock(side_effect=ConnectionError("timeout"))
        mock_client_cls.return_value = mock_client

        process_job(conn, 3, _payload())

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

        process_job(conn, 4, _payload())

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

        process_job(conn, 5, _payload())

    sql, params = conn._cursor.calls[0]
    assert "ghp_" not in params[0]
    assert "[redacted]" in params[0]


def test_process_job_rejects_invalid_payload_without_calling_github():
    conn = _FakeConn()
    bad_payload = json.dumps({"owner": "acme"})  # missing repo/token

    with patch("worker.httpx.Client") as mock_client_cls:
        process_job(conn, 9, bad_payload)
        mock_client_cls.assert_not_called()

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert params[1] == 9
    assert conn.committed is True


def test_process_job_rejects_empty_string_fields():
    conn = _FakeConn()
    bad_payload = json.dumps({"owner": "", "repo": "demo", "token": "x"})

    with patch("worker.httpx.Client") as mock_client_cls:
        process_job(conn, 10, bad_payload)
        mock_client_cls.assert_not_called()

    sql, params = conn._cursor.calls[0]
    assert "status='failed'" in sql
    assert conn.committed is True
