"""Tests for individual GitHub security checks."""

from unittest.mock import patch

import httpx
import pytest

from checks.github_checks import BranchProtectionEnabled, OrgMFARequired, SecretScanningEnabled, _get


def test_org_mfa_missing_field_returns_error():
    check = OrgMFARequired()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", lambda url, token: {"login": "acme"})
        result = check.run(owner="acme", token="tok")
    assert result["status"] == "error"


def test_secret_scanning_null_security_and_analysis_does_not_crash():
    check = SecretScanningEnabled()
    repos = [{"name": "demo", "security_and_analysis": None}]
    result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] in {"pass", "fail"}


def test_secret_scanning_empty_org_is_not_applicable():
    check = SecretScanningEnabled()
    result = check.run(owner="acme", token="tok", repos=[])
    assert result["status"] == "not_applicable"
    assert result["value"] == {"enabled": 0, "total": 0}


def test_branch_protection_empty_org_is_not_applicable():
    check = BranchProtectionEnabled()
    result = check.run(owner="acme", token="tok", repos=[])
    assert result["status"] == "not_applicable"
    assert result["value"] == {"checked": 0, "protected": 0, "unknown": 0}


def test_branch_protection_rate_limit_counts_as_unknown():
    check = BranchProtectionEnabled()
    repos = [{"name": "demo", "default_branch": "main"}]

    def fake_get(url, token):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"
    assert result["value"]["unknown"] == 1


def test_branch_protection_network_error_counts_as_unknown():
    check = BranchProtectionEnabled()
    repos = [{"name": "demo", "default_branch": "main"}]

    def fake_get(url, token):
        raise httpx.ConnectError("network down", request=httpx.Request("GET", url))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"
    assert result["value"]["unknown"] == 1


def test_get_retries_on_transient_connection_error_then_succeeds():
    # Matches GitHubClient.request's retry contract (apps/api/src/services/github_client.py):
    # a transient RequestError shouldn't fail the call outright.
    ok_response = httpx.Response(200, json={"login": "acme"}, request=httpx.Request("GET", "https://x/y"))
    call_count = 0

    def fake_get(url, headers):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("network down", request=httpx.Request("GET", url))
        return ok_response

    with patch("time.sleep"), patch("httpx.Client.get", side_effect=fake_get):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}
    assert call_count == 2


def test_get_retries_on_429_then_succeeds():
    request = httpx.Request("GET", "https://x/y")
    rate_limited = httpx.Response(429, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep"), patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}


def test_get_gives_up_after_3_attempts_on_persistent_connection_error():
    def fake_get(url, headers):
        raise httpx.ConnectError("network down", request=httpx.Request("GET", url))

    with patch("time.sleep"), patch("httpx.Client.get", side_effect=fake_get) as mock_get:
        with pytest.raises(httpx.ConnectError):
            _get("https://x/y", "tok")
    assert mock_get.call_count == 3
