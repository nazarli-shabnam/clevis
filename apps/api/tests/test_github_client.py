"""Tests for GitHubClient.request — covers B-03 (single client) and B-04 (no silent None)."""
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from src.services.github_client import GitHubClient


@pytest.fixture()
def client():
    # Pass base_url directly so the test doesn't need a DB connection
    yield GitHubClient(token="ghp_test", base_url="https://api.github.com")


def _make_response(status_code: int, json_body: dict | list | None = None, text: str = "{}"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text if json_body is None else str(json_body)
    resp.json.return_value = json_body if json_body is not None else {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestSingleClient:
    """B-03: httpx.Client instantiated once outside retry loop."""

    def test_one_client_per_request_call(self, client):
        ok_resp = _make_response(200, {"id": 1})
        with patch("src.services.github_client.httpx.Client") as mock_client_cls:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.request.return_value = ok_resp
            mock_client_cls.return_value = mock_ctx

            result = client.request("GET", "/repos/acme/api")

        # Client constructor called exactly once regardless of retries
        mock_client_cls.assert_called_once_with(timeout=20)
        assert result == {"id": 1}

    def test_retries_reuse_same_client(self, client):
        """On a 429, the second attempt should use the same client instance."""
        resp_429 = _make_response(429)
        resp_429.raise_for_status = MagicMock()  # 429 is not raise_for_status'd immediately
        ok_resp = _make_response(200, {"ok": True})

        with (
            patch("src.services.github_client.httpx.Client") as mock_client_cls,
            patch("src.services.github_client.time.sleep"),
        ):
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.request.side_effect = [resp_429, ok_resp]
            mock_client_cls.return_value = mock_ctx

            result = client.request("GET", "/test")

        mock_client_cls.assert_called_once()
        assert result == {"ok": True}


class TestExhaustedRetries:
    """B-04: exhausted retries must raise, not return None."""

    def test_request_error_raises_after_three_attempts(self, client):
        with (
            patch("src.services.github_client.httpx.Client") as mock_client_cls,
            patch("src.services.github_client.time.sleep"),
        ):
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.request.side_effect = httpx.RequestError("connection failed")
            mock_client_cls.return_value = mock_ctx

            with pytest.raises(httpx.RequestError):
                client.request("GET", "/test")

    def test_success_on_second_attempt_returns_value(self, client):
        err_resp = _make_response(429)
        err_resp.raise_for_status = MagicMock()
        ok_resp = _make_response(200, {"data": "hello"})

        with (
            patch("src.services.github_client.httpx.Client") as mock_client_cls,
            patch("src.services.github_client.time.sleep"),
        ):
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_ctx.request.side_effect = [err_resp, ok_resp]
            mock_client_cls.return_value = mock_ctx

            result = client.request("GET", "/test")

        assert result == {"data": "hello"}
