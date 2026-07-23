"""Tests for the security compliance matrix and secret-scanning routes (docs/plan.md Phase 16)."""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.routers.security import router

_USER = UserOut(id=1, email="u@example.com", name=None, is_workspace_admin=False)


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: _USER
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_security_matrix_requires_auth(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get("/me/analytics/security-matrix/acme")
    assert resp.status_code == 401


def test_security_matrix_no_token_returns_400(client):
    resp = client.get("/me/analytics/security-matrix/acme")
    assert resp.status_code == 400


def test_security_matrix_computes_rows_and_summary(client):
    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            return [{"security_advisory": {"severity": "critical"}}]
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ]
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    body = resp.json()
    row = body["repos"][0]
    assert row["repo"] == "api"
    assert row["branch_protection"] is True
    assert row["secret_scanning"] is True
    assert row["dependabot_critical_count"] == 1
    assert row["code_scanning"] is True
    assert row["force_push_allowed"] is False
    assert row["score"] == 80  # 4 of 5 dimensions pass (dependabot has a critical alert)
    assert row["unknown_dimensions"] == []
    assert body["summary"]["critical_risk_count"] == 1
    assert body["summary"]["vuln_by_severity"]["critical"] == 1
    assert body["summary"]["fully_compliant_count"] == 0


def test_security_matrix_excludes_unknown_dimensions_from_score(client):
    """A 403/network error must not be scored as if the dimension were compliant --
    see the DependabotAlertsCheck false-pass fix (packages/checks, 3184c76) this
    mirrors. Every GitHub call fails here, so only secret_scanning (read from the
    already-fetched repo list, no extra call) is evaluable."""
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {"name": "api", "default_branch": "main", "security_and_analysis": {}},
        ]
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    row = resp.json()["repos"][0]
    assert row["branch_protection"] is False
    assert row["dependabot_enabled"] is False
    assert sorted(row["unknown_dimensions"]) == ["branch_protection", "code_scanning", "dependabot", "force_push"]
    assert row["score"] == 0  # secret_scanning is the only evaluable dimension, and it's False


def test_security_matrix_403_on_dependabot_is_unknown_not_clean(client):
    """A 403 (missing security-events scope) must not read as 'no critical/high
    alerts' -- that's the exact bug fixed for DependabotAlertsCheck in 3184c76."""
    forbidden = httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://api.github.com/x"),
        response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/x")),
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/dependabot/alerts"):
            raise forbidden
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ]
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["unknown_dimensions"] == ["dependabot"]
    assert row["score"] == 100  # remaining 4 evaluable dimensions all pass
    assert row["dependabot_critical_count"] == 0  # not silently zero-and-clean -- flagged unknown instead


def test_security_matrix_404_on_dependabot_is_genuinely_disabled(client):
    """Unlike a 403, a 404 is a real 'Dependabot is off for this repo' answer and
    should count as a real (non-unknown) 'no alerts' pass."""
    not_found = httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://api.github.com/x"),
        response=httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x")),
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/dependabot/alerts"):
            raise not_found
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ]
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["unknown_dimensions"] == []
    assert row["score"] == 100


def test_secret_scanning_no_token_returns_400(client):
    resp = client.get("/me/repos/acme/demo/secret-scanning")
    assert resp.status_code == 400


def test_secret_scanning_never_includes_secret_value(client):
    raw_alert = {
        "number": 1,
        "state": "open",
        "secret_type": "github_personal_access_token",
        "secret_type_display_name": "GitHub Personal Access Token",
        "created_at": "2026-07-01T00:00:00Z",
        "resolved_at": None,
        "resolution": None,
        "html_url": "https://github.com/acme/demo/security/secret-scanning/1",
        "secret": "ghp_thisShouldNeverAppear1234567890",
    }
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.return_value = [raw_alert]
        resp = client.get("/me/repos/acme/demo/secret-scanning", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    body_text = resp.text
    assert "ghp_thisShouldNeverAppear1234567890" not in body_text
    alert = resp.json()["alerts"][0]
    assert alert["secret_type"] == "github_personal_access_token"
    assert alert["secret_type_display"] == "GitHub Personal Access Token"
    assert "secret" not in alert


def test_secret_scanning_skips_malformed_entries(client):
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.return_value = [{"state": "open"}]  # missing number/created_at
        resp = client.get("/me/repos/acme/demo/secret-scanning", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["alerts"] == []


def test_secret_scanning_github_error_maps_to_400(client):
    error = httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://api.github.com/x"),
        response=httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/x")),
    )
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = error
        resp = client.get("/me/repos/acme/demo/secret-scanning", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 400
