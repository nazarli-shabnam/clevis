"""Tests for individual GitHub security checks."""

from unittest.mock import patch

import httpx
import pytest

from checks.github_checks import (
    _MAX_PAGES,
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


# ── _get_all_pages pagination cap (issue #214: unbounded pagination -> OOM) ────


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

    # Stops at the cap rather than following the (still-present) Link header forever --
    # this is the behavior that prevents an unbounded in-memory list on a huge org.
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
    # A token lacking the security-events scope gets a 403 (not 404) from every
    # repo -- that must surface as "unknown", not silently render as "clear".
    check = DependabotAlertsCheck()
    repos = [{"name": "api"}, {"name": "ui"}]

    def fake_get(url, token):
        response = httpx.Response(403, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("forbidden", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"


def test_dependabot_mixed_403_and_clean_repos_still_passes():
    # Only some repos are forbidden -- the rest were genuinely evaluated as clear,
    # so this should NOT be forced to "error".
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
    assert result["status"] == "pass"


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
        return {"protection": {"allow_force_pushes": {"enabled": False}}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "pass"
    assert result["value"] == {"repos_checked": 1, "force_push_allowed": 0}


def test_force_push_fails_when_allowed():
    check = DefaultBranchNoForcePushCheck()
    repos = [{"name": "api", "default_branch": "main"}]

    def fake_get(url, token):
        return {"protection": {"allow_force_pushes": {"enabled": True}}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "fail"
    assert result["value"] == {"repos_checked": 1, "force_push_allowed": 1}


def test_force_push_unprotected_branch_excluded_from_denominator():
    check = DefaultBranchNoForcePushCheck()
    repos = [{"name": "api", "default_branch": "main"}]

    def fake_get(url, token):
        response = httpx.Response(404, request=httpx.Request("GET", url))
        raise httpx.HTTPStatusError("not found", request=response.request, response=response)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("checks.github_checks._get", fake_get)
        result = check.run(owner="acme", token="tok", repos=repos)
    assert result["status"] == "error"
    assert result["value"] == {"repos_checked": 0, "force_push_allowed": 0}
