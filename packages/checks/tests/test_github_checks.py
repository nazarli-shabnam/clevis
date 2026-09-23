"""Tests for individual GitHub security checks."""

import time
from unittest.mock import patch

import httpx
import pytest

from checks.github_checks import (
    _MAX_PAGES,
    _MAX_RETRY_AFTER_SECONDS,
    BranchProtectionEnabled,
    CodeScanningCheck,
    DefaultBranchNoForcePushCheck,
    DependabotAlertsCheck,
    OrgMFARequired,
    SecretScanningEnabled,
    _get,
    _get_all_pages,
)


def test_org_mfa_missing_field_returns_error():
    check = OrgMFARequired()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", lambda url, token: {"login": "acme"})
        result = check.run(owner="acme", token="tok")
    assert result["status"] == "error"


def test_org_mfa_not_applicable_for_personal_account():
    """A personal account has no MFA requirement setting; no GitHub call is made."""
    check = OrgMFARequired()

    def _fail_if_called(url, token):
        raise AssertionError("MFA check must not call GitHub for a personal account")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", _fail_if_called)
        result = check.run(owner="octocat", token="tok", account_type="User")
    assert result["status"] == "not_applicable"


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


def test_branch_protection_5xx_counts_as_unknown_not_unprotected():
    # A transient 5xx must be "unknown", not "unprotected".
    check = BranchProtectionEnabled()
    repos = [{"name": "demo", "default_branch": "main"}]

    def fake_get(url, token):
        response = httpx.Response(500, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("server error", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"
    assert result["value"]["unknown"] == 1
    assert result["value"]["protected"] == 0


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
    # A transient RequestError shouldn't fail the call outright.
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


def test_get_retries_on_secondary_rate_limit_403_then_succeeds():
    # GitHub's secondary rate limit returns 403 (not 429) with Retry-After.
    request = httpx.Request("GET", "https://x/y")
    rate_limited = httpx.Response(403, headers={"Retry-After": "1"}, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep"), patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}


def test_get_retries_on_5xx_then_succeeds():
    # A 5xx is presumed transient and retried.
    request = httpx.Request("GET", "https://x/y")
    server_error = httpx.Response(502, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([server_error, ok_response])

    with patch("time.sleep"), patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}


def test_get_honors_the_actual_retry_after_value_not_a_blind_backoff():
    # The Retry-After value itself must be honored, not a fixed 2**attempt.
    request = httpx.Request("GET", "https://x/y")
    rate_limited = httpx.Response(429, headers={"Retry-After": "5"}, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep") as mock_sleep, patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}
    mock_sleep.assert_called_once_with(5.0)


def test_get_caps_an_excessive_retry_after_value():
    request = httpx.Request("GET", "https://x/y")
    rate_limited = httpx.Response(429, headers={"Retry-After": "600"}, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep") as mock_sleep, patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        _get("https://x/y", "tok")
    mock_sleep.assert_called_once_with(_MAX_RETRY_AFTER_SECONDS)


def test_get_falls_back_to_ratelimit_reset_when_no_retry_after():
    # With no Retry-After, GitHub's docs say to wait until X-RateLimit-Reset (epoch seconds).
    request = httpx.Request("GET", "https://x/y")
    reset_at = time.time() + 30
    rate_limited = httpx.Response(
        429, headers={"X-RateLimit-Reset": str(int(reset_at))}, request=request
    )
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep") as mock_sleep, patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}
    slept = mock_sleep.call_args[0][0]
    assert 20 <= slept <= 30


def test_get_ignores_a_malformed_ratelimit_reset_and_waits_the_cap():
    # A non-numeric X-RateLimit-Reset must not raise; it falls through to the cap.
    request = httpx.Request("GET", "https://x/y")
    rate_limited = httpx.Response(429, headers={"X-RateLimit-Reset": "soon"}, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep") as mock_sleep, patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        result = _get("https://x/y", "tok")
    assert result == {"login": "acme"}
    mock_sleep.assert_called_once_with(_MAX_RETRY_AFTER_SECONDS)


def test_get_waits_the_cap_for_a_ratelimit_with_no_usable_header():
    # A 429 with neither header waits the full documented minimum backoff.
    request = httpx.Request("GET", "https://x/y")
    rate_limited = httpx.Response(429, request=request)
    ok_response = httpx.Response(200, json={"login": "acme"}, request=request)
    responses = iter([rate_limited, ok_response])

    with patch("time.sleep") as mock_sleep, patch("httpx.Client.get", side_effect=lambda url, headers: next(responses)):
        _get("https://x/y", "tok")
    mock_sleep.assert_called_once_with(_MAX_RETRY_AFTER_SECONDS)


def test_get_does_not_retry_a_plain_permission_denied_403():
    # A permission-denied 403 (no rate-limit headers) must surface immediately, not retry.
    request = httpx.Request("GET", "https://x/y")
    forbidden = httpx.Response(403, request=request)

    with (
        patch("time.sleep") as mock_sleep,
        patch("httpx.Client.get", return_value=forbidden) as mock_get,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            _get("https://x/y", "tok")

    mock_sleep.assert_not_called()
    assert mock_get.call_count == 1


def test_get_gives_up_after_3_attempts_on_persistent_connection_error():
    def fake_get(url, headers):
        raise httpx.ConnectError("network down", request=httpx.Request("GET", url))

    with patch("time.sleep"), patch("httpx.Client.get", side_effect=fake_get) as mock_get:
        with pytest.raises(httpx.ConnectError):
            _get("https://x/y", "tok")
    assert mock_get.call_count == 3


# ── _get_all_pages pagination cap ────


def test_get_all_pages_follows_link_header_across_pages():
    def fake_get(url, headers):
        page = int(url.split("&page=")[-1]) if "&page=" in url else 1
        if page < 3:
            link = f'<https://x/y?per_page=100&page={page + 1}>; rel="next"'
            return httpx.Response(200, json=[{"id": page}], headers={"Link": link}, request=httpx.Request("GET", url))
        return httpx.Response(200, json=[{"id": page}], request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=fake_get):
        results = _get_all_pages("https://x", "/y", "tok")
    assert results == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_get_all_pages_truncates_after_max_pages_and_logs_warning(caplog):
    call_count = 0

    def fake_get(url, headers):
        nonlocal call_count
        call_count += 1
        link = '<https://x/y?page=next>; rel="next"'
        return httpx.Response(200, json=[{"id": call_count}], headers={"Link": link}, request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=fake_get):
        with caplog.at_level("WARNING"):
            results = _get_all_pages("https://x", "/y", "tok")

    # Stops at the cap even though the Link header is still present.
    assert len(results) == _MAX_PAGES
    assert call_count == _MAX_PAGES
    assert "Truncating pagination" in caplog.text


# ── DependabotAlertsCheck ────────────────────────────────────────────────────


def test_dependabot_empty_org_is_not_applicable():
    check = DependabotAlertsCheck()
    result = check.run(owner="acme", token="tok", repos=[])
    assert result["status"] == "not_applicable"
    assert result["value"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_dependabot_aggregates_severity_counts_across_repos():
    check = DependabotAlertsCheck()
    repos = [{"name": "api"}, {"name": "ui"}]
    responses = {
        "api": [{"security_advisory": {"severity": "critical"}}, {"security_advisory": {"severity": "low"}}],
        "ui": [{"security_advisory": {"severity": "high"}}],
    }

    def fake_get(url, token):
        for name, alerts in responses.items():
            if f"/{name}/dependabot/alerts" in url:
                return alerts
        raise AssertionError(f"unexpected url {url}")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"
    assert result["value"] == {"critical": 1, "high": 1, "medium": 0, "low": 1}


def test_dependabot_passes_when_no_critical_or_high():
    check = DependabotAlertsCheck()
    repos = [{"name": "api"}]

    def fake_get(url, token):
        return [{"security_advisory": {"severity": "low"}}]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "pass"


def test_dependabot_disabled_repo_404_counts_as_zero_alerts():
    check = DependabotAlertsCheck()
    repos = [{"name": "api"}]

    def fake_get(url, token):
        response = httpx.Response(404, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("not found", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "pass"
    assert result["value"] == {"critical": 0, "high": 0, "medium": 0, "low": 0}


def test_dependabot_all_repos_forbidden_returns_error_not_false_pass():
    # A 403 from every repo (missing scope) must surface as "unknown", not "clear".
    check = DependabotAlertsCheck()
    repos = [{"name": "api"}, {"name": "ui"}]

    def fake_get(url, token):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"


def test_dependabot_mixed_403_and_otherwise_clean_repos_reports_error_not_false_pass():
    # 403 means unknown, not zero: a clean result from only reachable repos isn't "pass".
    check = DependabotAlertsCheck()
    repos = [{"name": "forbidden"}, {"name": "clean"}]

    def fake_get(url, token):
        if "/forbidden/" in url:
            response = httpx.Response(403, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)
        return []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"


def test_dependabot_mixed_403_and_a_real_alert_still_fails():
    # A "fail" from visible repos stays valid regardless of unknowns elsewhere.
    check = DependabotAlertsCheck()
    repos = [{"name": "forbidden"}, {"name": "vulnerable"}]

    def fake_get(url, token):
        if "/forbidden/" in url:
            response = httpx.Response(403, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)
        return [{"security_advisory": {"severity": "critical"}}]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"


def test_dependabot_non_404_403_error_propagates():
    check = DependabotAlertsCheck()
    repos = [{"name": "api"}]

    def fake_get(url, token):
        response = httpx.Response(500, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("server error", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        with pytest.raises(httpx.HTTPStatusError):
            check.run(owner="acme", token="tok", repos=repos)


# ── CodeScanningCheck ────────────────────────────────────────────────────────


def test_code_scanning_empty_org_is_not_applicable():
    check = CodeScanningCheck()
    result = check.run(owner="acme", token="tok", repos=[])
    assert result["status"] == "not_applicable"
    assert result["value"] == {"open": 0, "repos_with_alerts": 0, "total_repos": 0}


def test_code_scanning_counts_open_alerts_and_affected_repos():
    check = CodeScanningCheck()
    repos = [{"name": "api"}, {"name": "ui"}]

    def fake_get(url, token):
        if "/api/" in url:
            return [{"number": 1}, {"number": 2}]
        return []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"
    assert result["value"] == {"open": 2, "repos_with_alerts": 1, "total_repos": 2}


def test_code_scanning_disabled_repo_404_counts_as_zero_alerts():
    check = CodeScanningCheck()
    repos = [{"name": "api"}]

    def fake_get(url, token):
        response = httpx.Response(404, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("not found", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "pass"
    assert result["value"] == {"open": 0, "repos_with_alerts": 0, "total_repos": 1}


def test_code_scanning_all_repos_forbidden_returns_error_not_false_pass():
    check = CodeScanningCheck()
    repos = [{"name": "api"}]

    def fake_get(url, token):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"


def test_code_scanning_mixed_403_and_otherwise_clean_repos_reports_error_not_false_pass():
    # Same as the Dependabot equivalent: 403 means unknown, not zero.
    check = CodeScanningCheck()
    repos = [{"name": "forbidden"}, {"name": "clean"}]

    def fake_get(url, token):
        if "/forbidden/" in url:
            response = httpx.Response(403, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)
        return []

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"


def test_code_scanning_mixed_403_and_a_real_alert_still_fails():
    check = CodeScanningCheck()
    repos = [{"name": "forbidden"}, {"name": "vulnerable"}]

    def fake_get(url, token):
        if "/forbidden/" in url:
            response = httpx.Response(403, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)
        return [{"number": 1}]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"


# ── DefaultBranchNoForcePushCheck ────────────────────────────────────────────


def test_force_push_empty_org_is_not_applicable():
    check = DefaultBranchNoForcePushCheck()
    result = check.run(owner="acme", token="tok", repos=[])
    assert result["status"] == "not_applicable"
    assert result["value"] == {"repos_checked": 0, "force_push_allowed": 0}


def test_force_push_passes_when_disallowed():
    check = DefaultBranchNoForcePushCheck()
    repos = [{"name": "api", "default_branch": "main"}]

    def fake_get(url, token):
        # allow_force_pushes only lives on the /protection sub-resource.
        assert url.endswith("/repos/acme/api/branches/main/protection")
        return {"allow_force_pushes": {"enabled": False}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "pass"
    assert result["value"] == {"repos_checked": 1, "force_push_allowed": 0}


def test_force_push_fails_when_allowed():
    check = DefaultBranchNoForcePushCheck()
    repos = [{"name": "api", "default_branch": "main"}]

    def fake_get(url, token):
        assert url.endswith("/repos/acme/api/branches/main/protection")
        return {"allow_force_pushes": {"enabled": True}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"
    assert result["value"] == {"repos_checked": 1, "force_push_allowed": 1}


def test_force_push_unprotected_branch_404_means_force_push_is_allowed():
    # A 404 ("Branch not protected") means force pushes are allowed, so "fail", not "error".
    check = DefaultBranchNoForcePushCheck()
    repos = [{"name": "api", "default_branch": "main"}]

    def fake_get(url, token):
        response = httpx.Response(404, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("not found", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"
    assert result["value"] == {"repos_checked": 1, "force_push_allowed": 1}


def test_force_push_rate_limited_branch_excluded_from_denominator():
    # A 403/429 can't be evaluated, so it's excluded from the denominator.
    check = DefaultBranchNoForcePushCheck()
    repos = [{"name": "api", "default_branch": "main"}]

    def fake_get(url, token):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"
    assert result["value"] == {"repos_checked": 0, "force_push_allowed": 0}
