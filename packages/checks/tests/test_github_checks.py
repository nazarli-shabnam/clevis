"""Tests for individual GitHub security checks."""

import httpx
import pytest

from checks.github_checks import BranchProtectionEnabled, OrgMFARequired, SecretScanningEnabled


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
    assert result["status"] == "error"
    assert result["value"]["unknown"] == 1


def test_secret_scanning_empty_org_is_not_applicable():
    check = SecretScanningEnabled()
    result = check.run(owner="acme", token="tok", repos=[])
    assert result["status"] == "not_applicable"


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
