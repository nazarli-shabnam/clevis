"""Unit tests for analytics_service.get_account_type (issue #144)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.analytics_service import get_account_type


def _mock_response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_get_account_type_returns_organization():
    with patch("httpx.Client.get", return_value=_mock_response({"type": "Organization"})):
        assert get_account_type("acme", "tok", base_url="https://api.github.com") == "Organization"


def test_get_account_type_returns_user():
    with patch("httpx.Client.get", return_value=_mock_response({"type": "User"})):
        assert get_account_type("octocat", "tok", base_url="https://api.github.com") == "User"


def test_get_account_type_raises_on_404():
    with patch("httpx.Client.get", return_value=_mock_response({}, status_code=404)):
        with pytest.raises(httpx.HTTPStatusError):
            get_account_type("missing", "tok", base_url="https://api.github.com")
