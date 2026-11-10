"""Tests for the security compliance matrix and secret-scanning routes."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.routers.security import router

_USER = UserOut(id=1, email="u@example.com", name=None, is_workspace_admin=False)


def _branch_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/x")
    return httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status_code, request=request))


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)



def _set_repos(mock_client, repos):
    """Make the mocked client's request_paginated return `repos` for the repo listing and delegate the
    per-repo Dependabot alert listing to the test's own `request` side effect, as one call returns it all."""
    inst = mock_client.return_value

    def paginated(path, params=None, items_key=None):
        if path.endswith("/dependabot/alerts"):
            return inst.request("GET", path, params)
        return repos

    inst.request_paginated.side_effect = paginated

@pytest.fixture()
def mock_user(db):
    return _make_user(db, "security-matrix@example.com")


@pytest.fixture()
def acme_org_with_installation(db, mock_user):
    """Connected tenant: caller is an org member and the org has an installation_id."""
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=7, org_id=org.id
    )
    return org


def _insert_security_alert(db, tenant_id, *, repo, kind, number, state, severity, details, synced=True, synced_days_ago=0):
    """`synced` also records that the alert backfill read this (repo, kind) in full, which is what lets the
    matrix use the stored rows; pass False for rows that only arrived by webhook."""
    db.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))
    if synced:
        db.execute(
            text(
                "INSERT INTO security_alert_syncs (tenant_id, repo, kind, synced_at) "
                "VALUES (:tenant_id, :repo, :kind, now() - make_interval(days => :ago)) ON CONFLICT DO NOTHING"
            ),
            {"tenant_id": tenant_id, "repo": repo, "kind": kind, "ago": synced_days_ago},
        )
    db.execute(
        text(
            "INSERT INTO security_alerts (tenant_id, repo, kind, number, state, severity, details, created_at, updated_at) "
            "VALUES (:tenant_id, :repo, :kind, :number, :state, :severity, :details, :now, :now)"
        ),
        {
            "tenant_id": tenant_id,
            "repo": repo,
            "kind": kind,
            "number": number,
            "state": state,
            "severity": severity,
            "details": json.dumps(details),
            "now": datetime.now(timezone.utc),
        },
    )
    db.commit()


@pytest.fixture(autouse=True)
def _default_account_type():
    # _build_matrix branches on account_type; default to "Organization" unless a test overrides it.
    with patch("src.routers.security.get_account_type", return_value="Organization"):
        yield


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: _USER
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture()
def connected_client(db, mock_user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: mock_user
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
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            return [{"security_advisory": {"severity": "critical"}}]
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
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
    """A 403/network error must not score the dimension as compliant.

    Every GitHub call fails, so only secret_scanning (from the repo list) is evaluable.
    """
    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {}},
        ])
        mock_client.return_value.request.side_effect = httpx.RequestError("boom")
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    row = resp.json()["repos"][0]
    assert row["branch_protection"] is False
    assert row["dependabot_enabled"] is False
    assert sorted(row["unknown_dimensions"]) == ["branch_protection", "code_scanning", "dependabot", "force_push"]
    assert row["score"] == 0  # secret_scanning is the only evaluable dimension, and it's False


@pytest.mark.parametrize("status_code, unknown", [(401, True), (403, True), (429, True), (502, True), (404, False)])
def test_security_matrix_branch_protection_only_404_is_unprotected(client, status_code, unknown):
    """A 5xx/401 from the branch call is no answer, not 'unprotected'; only a 404 is a real negative."""
    def _request_side_effect(method, path, params=None):
        if "/branches/" in path:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("GET", "https://api.github.com/x"),
                response=httpx.Response(status_code, request=httpx.Request("GET", "https://api.github.com/x")),
            )
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [{"name": "api", "default_branch": "main"}])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert ("branch_protection" in row["unknown_dimensions"]) is unknown
    assert ("force_push" in row["unknown_dimensions"]) is unknown


def test_security_matrix_repo_without_default_branch_skips_branch_call(client):
    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [{"name": "empty", "default_branch": None}])
        mock_client.return_value.request.return_value = {}
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    paths = [c.args[1] for c in mock_client.return_value.request.call_args_list]
    assert not any("/branches/" in p for p in paths)
    assert sorted(resp.json()["repos"][0]["unknown_dimensions"]) == ["branch_protection", "force_push"]
    assert resp.json()["summary"]["fully_compliant_count"] == 0  # unknown dimensions can't be "fully compliant"


def test_security_matrix_403_on_dependabot_is_unknown_not_clean(client):
    """A 403 (missing security-events scope) must not read as 'no critical/high alerts'."""
    forbidden = _branch_error(403)

    def _request_side_effect(method, path, params=None):
        if path.endswith("/dependabot/alerts"):
            raise forbidden
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["unknown_dimensions"] == ["dependabot"]
    assert row["score"] == 100  # remaining 4 evaluable dimensions all pass
    assert row["dependabot_critical_count"] == 0  # not silently zero-and-clean -- flagged unknown instead
    assert resp.json()["summary"]["fully_compliant_count"] == 0  # score 100 but one dimension unknown


def test_security_matrix_404_on_dependabot_is_genuinely_disabled(client):
    """Unlike a 403, a 404 means Dependabot is off and counts as a real 'no alerts' pass."""
    not_found = _branch_error(404)

    def _request_side_effect(method, path, params=None):
        if path.endswith("/dependabot/alerts"):
            raise not_found
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["unknown_dimensions"] == []
    assert row["score"] == 100


def _matrix_for_branch_response(client, branch_result):
    """Run the matrix for one repo whose only varying call is the branch lookup."""

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main"):
            if isinstance(branch_result, Exception):
                raise branch_result
            return branch_result
        if path.endswith("/code-scanning/alerts"):
            return []
        if path.endswith("/dependabot/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 502, 503])
def test_security_matrix_non_404_branch_error_is_unknown_not_unprotected(client, status_code):
    """Only a 404 is a real 'unprotected' answer; a 401 or a GitHub 5xx says nothing about the repo."""
    body = _matrix_for_branch_response(client, _branch_error(status_code))
    row = body["repos"][0]

    assert sorted(row["unknown_dimensions"]) == ["branch_protection", "force_push"]
    # Unknown dimensions are left out of the score instead of counting against the repo...
    assert row["score"] == 100
    # ...but a repo whose protection was never verified is not reported as fully compliant.
    assert body["summary"]["fully_compliant_count"] == 0


def test_security_matrix_404_branch_is_genuinely_unprotected(client):
    row = _matrix_for_branch_response(client, _branch_error(404))["repos"][0]

    assert row["branch_protection"] is False
    assert row["unknown_dimensions"] == []
    assert row["score"] == 80  # branch_protection is the one failing evaluable dimension


@pytest.mark.parametrize("default_branch", [None, ""])
def test_security_matrix_repo_without_default_branch_skips_branch_lookup(client, default_branch):
    """No default branch means nothing to look up: never request /branches/None."""
    requested_paths: list[str] = []

    def _request_side_effect(method, path, params=None):
        requested_paths.append(path)
        return []

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "empty", "default_branch": default_branch, "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    row = resp.json()["repos"][0]
    assert not any("/branches/" in path for path in requested_paths)
    assert sorted(row["unknown_dimensions"]) == ["branch_protection", "force_push"]
    assert row["score"] == 100
    assert resp.json()["summary"]["fully_compliant_count"] == 0


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


def test_security_matrix_uses_aggregate_when_installation_connected(connected_client, db, acme_org_with_installation):
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=1,
        state="open", severity="critical", details={"dependency": {}},
    )
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="code_scanning", number=2,
        state="open", severity="error", details={"rule": {}},
    )
    # Only 'open' rows are live findings, matching the live path's state=open query.
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=3,
        state="dismissed", severity="critical", details={"dependency": {}},
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        raise AssertionError(f"unexpected live GitHub call for a connected org: {path}")

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    row = resp.json()["repos"][0]
    assert row["alerts_source"] == "aggregate"
    assert row["dependabot_enabled"] is True
    assert row["dependabot_critical_count"] == 1  # the dismissed one doesn't count
    assert row["dependabot_high_count"] == 0
    assert row["code_scanning"] is False  # an open code_scanning alert exists
    assert row["unknown_dimensions"] == []


def test_security_matrix_asks_github_for_a_repo_whose_alerts_were_only_partly_ingested(connected_client, db, acme_org_with_installation):
    """A repo with an old critical alert that predates the App, plus one new low alert that arrived by webhook,
    must not be reported from the one stored row: its alerts were never read in full (#814)."""
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=9,
        state="open", severity="low", details={"dependency": {}}, synced=False,
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            return [{"security_advisory": {"severity": "critical"}}, {"security_advisory": {"severity": "low"}}]
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ]
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["alerts_source"] == "github"
    assert row["dependabot_critical_count"] == 1


def test_security_matrix_asks_github_again_once_a_sync_record_has_aged_out(connected_client, db, acme_org_with_installation):
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=1,
        state="open", severity="low", details={"dependency": {}}, synced_days_ago=31,
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/dependabot/alerts"):
            return [{"security_advisory": {"severity": "critical"}}]
        if path.endswith("/branches/main"):
            return {"protected": True}
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [
            {"name": "api", "default_branch": "main", "security_and_analysis": {}},
        ]
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["alerts_source"] == "github" and row["dependabot_critical_count"] == 1


def test_security_matrix_trusts_stored_alerts_per_kind_once_that_kind_is_synced(connected_client, db, acme_org_with_installation):
    tenant = acme_org_with_installation.tenant_id
    _insert_security_alert(
        db, tenant, repo="acme/api", kind="dependabot", number=1, state="open", severity="critical", details={"dependency": {}},
    )  # dependabot synced
    _insert_security_alert(
        db, tenant, repo="acme/api", kind="code_scanning", number=2, state="open", severity="error", details={"rule": {}}, synced=False,
    )  # code scanning only seen by webhook

    seen = []

    def _request_side_effect(method, path, params=None):
        seen.append(path)
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
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
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["dependabot_critical_count"] == 1  # from the synced stored rows
    assert not any(p.endswith("/dependabot/alerts") for p in seen)  # no live Dependabot call
    assert any(p.endswith("/code-scanning/alerts") for p in seen)  # the unsynced kind was asked live
    assert row["code_scanning"] is True  # live answer: no open alerts


def test_security_matrix_aggregate_dependabot_enabled_with_only_dismissed_alerts(connected_client, db, acme_org_with_installation):
    """dependabot_enabled considers every state: a repo with only dismissed alerts still has Dependabot enabled."""
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=1,
        state="dismissed", severity="critical", details={"dependency": {}},
    )

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.return_value = {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["dependabot_enabled"] is True
    assert row["dependabot_critical_count"] == 0  # dismissed, not a live finding
    assert row["score"] == 100


def test_security_matrix_falls_back_to_live_for_a_repo_with_no_ingested_alert_rows(connected_client, db, acme_org_with_installation):
    """No security_alerts rows is ambiguous (clean vs. not yet ingested), so fall back to live GitHub."""
    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            return []
        if path.endswith("/code-scanning/alerts"):
            return []
        raise AssertionError(f"unexpected call: {path}")

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "clean-repo", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["alerts_source"] == "github"
    assert row["dependabot_enabled"] is True  # 200 with an empty list, not a 404 -- genuinely enabled
    assert row["code_scanning"] is True


def test_security_matrix_uses_aggregate_only_for_repos_with_ingested_rows(connected_client, db, acme_org_with_installation):
    """Each repo's alerts_source is decided independently, not once per tenant."""
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=1,
        state="open", severity="critical", details={"dependency": {}},
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            return []
        if path.endswith("/code-scanning/alerts"):
            return []
        raise AssertionError(f"unexpected call: {path}")

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
            {"name": "new-repo", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    rows = {r["repo"]: r for r in resp.json()["repos"]}
    assert rows["api"]["alerts_source"] == "aggregate"
    assert rows["api"]["dependabot_critical_count"] == 1
    assert rows["api"]["code_scanning"] is True  # no ingested code_scanning rows -- live path
    assert rows["new-repo"]["alerts_source"] == "github"


def test_security_matrix_gates_dependabot_and_code_scanning_independently(connected_client, db, acme_org_with_installation):
    """Ingested rows for one alert kind must not mark the other kind clean.

    Only a dependabot row is ingested, so code_scanning must still come from live GitHub.
    """
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=1,
        state="open", severity="high", details={"dependency": {}},
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            raise AssertionError("dependabot dimension should come from the aggregate, not a live call")
        if path.endswith("/code-scanning/alerts"):
            return [{"number": 9}]  # a real open alert, never ingested
        raise AssertionError(f"unexpected call: {path}")

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["alerts_source"] == "aggregate"  # dependabot was aggregate-sourced
    assert row["dependabot_high_count"] == 1
    assert row["code_scanning"] is False  # must reflect the real live open alert, not a false "clear"


def test_security_matrix_connected_org_with_no_repos_is_empty(connected_client, db, acme_org_with_installation):
    """No repos: _open_alerts_by_repo must not error on an empty IN (...) query."""
    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [])
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["repos"] == []


def test_security_matrix_falls_back_to_github_when_caller_lacks_membership(client, db):
    """Without an OrgMembership, _security_connected_tenant returns None and the matrix uses the live path."""
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=7, org_id=org.id
    )

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts") or path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.json()["repos"][0]["alerts_source"] == "github"


def test_security_matrix_falls_back_to_github_when_org_has_no_installation(connected_client, db, mock_user):
    """Member of an org with no App installation: _security_connected_tenant returns None."""
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=mock_user.id, role="member")

    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts") or path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = connected_client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.json()["repos"][0]["alerts_source"] == "github"


def test_security_matrix_unconnected_org_still_uses_live_github(client, db):
    """No installation for this owner: the matrix uses the live-GitHub path."""
    def _request_side_effect(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": False}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": False}}}
        if path.endswith("/dependabot/alerts"):
            return []
        if path.endswith("/code-scanning/alerts"):
            return []
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        _set_repos(mock_client, [
            {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}},
        ])
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["alerts_source"] == "github"


def test_secret_scanning_uses_aggregate_when_installation_connected(connected_client, db, acme_org_with_installation):
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/demo", kind="secret_scanning", number=1,
        state="open", severity=None,
        details={"secret_type": "github_personal_access_token", "secret_type_display_name": "GitHub Personal Access Token", "resolution": None},
    )
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/demo", kind="secret_scanning", number=2,
        state="resolved", severity=None,
        details={"secret_type": "aws_access_key_id", "secret_type_display_name": "AWS Access Key", "resolution": "revoked"},
    )

    with patch("src.routers.security.GitHubClient") as mock_client:
        resp = connected_client.get("/me/repos/acme/demo/secret-scanning", headers={"X-GitHub-Token": "ghp_test"})
        mock_client.return_value.request.assert_not_called()

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "aggregate"
    alerts = {a["number"]: a for a in body["alerts"]}
    assert alerts[1]["state"] == "open"
    assert alerts[1]["resolved_at"] is None
    assert alerts[2]["state"] == "resolved"
    assert alerts[2]["resolved_reason"] == "revoked"
    assert alerts[2]["resolved_at"] is not None
    assert "secret" not in alerts[1]
    # url is None (not ""): security_alerts doesn't store GitHub's html_url.
    assert alerts[1]["url"] is None


def test_secret_scanning_falls_back_to_live_when_aggregate_has_no_rows(connected_client, db, acme_org_with_installation):
    # security_alerts is webhook-only (no sync cursor), so an empty aggregate isn't authoritative.
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.return_value = [
            {"number": 7, "state": "open", "secret_type": "aws_access_key_id", "created_at": "2026-08-01T00:00:00Z"},
        ]
        resp = connected_client.get("/me/repos/acme/demo/secret-scanning", headers={"X-GitHub-Token": "ghp_test"})
        mock_client.return_value.request.assert_called_once()

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "github"
    assert [a["number"] for a in body["alerts"]] == [7]


# ── personal (User-type) account support ────────────────────────────────────────

def test_security_matrix_personal_account_uses_installation_repos_endpoint(client):
    """A personal account's repos come from /installation/repositories; /orgs/{owner}/repos 404s for Users."""
    with (
        patch("src.routers.security.get_account_type", return_value="User"),
        patch("src.routers.security.GitHubClient") as mock_client,
    ):
        _set_repos(mock_client, [
            {"name": "dotfiles", "default_branch": "main", "security_and_analysis": {}},
        ])
        mock_client.return_value.request.return_value = {}
        resp = client.get("/me/analytics/security-matrix/octocat", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["repos"][0]["repo"] == "dotfiles"
    mock_client.return_value.request_paginated.assert_any_call(
        "/installation/repositories", items_key="repositories"
    )


def test_security_matrix_personal_account_falls_back_to_user_repos_on_auth_mismatch(client):
    """A legacy PAT gets 401/403 from /installation/repositories, so fall back to /user/repos."""
    forbidden = httpx.HTTPStatusError(
        "boom", request=httpx.Request("GET", "https://api.github.com/installation/repositories"),
        response=httpx.Response(403, request=httpx.Request("GET", "https://api.github.com/installation/repositories")),
    )

    def _request_paginated_side_effect(path, params=None, items_key=None):
        if path == "/installation/repositories":
            raise forbidden
        return [{"name": "dotfiles", "default_branch": "main", "security_and_analysis": {}}]

    with (
        patch("src.routers.security.get_account_type", return_value="User"),
        patch("src.routers.security.GitHubClient") as mock_client,
    ):
        mock_client.return_value.request_paginated.side_effect = _request_paginated_side_effect
        mock_client.return_value.request.return_value = {}
        resp = client.get("/me/analytics/security-matrix/octocat", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["repos"][0]["repo"] == "dotfiles"


def test_repo_security_row_requires_auth(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    assert TestClient(app).get("/me/analytics/security-matrix/acme/api").status_code == 401


def test_repo_security_row_no_token_returns_400(client):
    assert client.get("/me/analytics/security-matrix/acme/api").status_code == 400


def test_repo_security_row_returns_just_that_repo_without_listing_the_org(client):
    def _request(method, path, params=None):
        if path == "/repos/acme/api":
            return {"name": "api", "default_branch": "main", "security_and_analysis": {"secret_scanning": {"status": "enabled"}}}
        if path.endswith("/branches/main/protection"):
            return {"allow_force_pushes": {"enabled": True}}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"allow_force_pushes": {"enabled": True}}}
        if path.endswith("/dependabot/alerts"):
            return [{"security_advisory": {"severity": "critical"}}, {"security_advisory": {"severity": "high"}}]
        if path.endswith("/code-scanning/alerts"):
            return [{"number": 1}]
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = _request
        mock_client.return_value.request_paginated.side_effect = lambda path, params=None, items_key=None: _request("GET", path, params)
        resp = client.get("/me/analytics/security-matrix/acme/api", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    row = resp.json()
    assert (row["repo"], row["dependabot_critical_count"], row["dependabot_high_count"]) == ("api", 1, 1)
    assert row["code_scanning"] is False
    assert row["force_push_allowed"] is True
    # The paginated reads are the ruleset lookup and the Dependabot alerts; the org's repo list is never fetched.
    assert sorted(c.args[0] for c in mock_client.return_value.request_paginated.call_args_list) == [
        "/repos/acme/api/dependabot/alerts",
        "/repos/acme/api/rules/branches/main",
    ]


def test_repo_security_row_uses_aggregate_alerts_when_connected(connected_client, db, acme_org_with_installation):
    _insert_security_alert(
        db, acme_org_with_installation.tenant_id, repo="acme/api", kind="dependabot", number=1,
        state="open", severity="critical", details={},
    )

    def _request(method, path, params=None):
        if path == "/repos/acme/api":
            return {"name": "api", "default_branch": "main"}
        if path.endswith("/branches/main/protection"):
            return {}
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {}}
        if path.endswith("/code-scanning/alerts"):
            return []
        raise AssertionError(f"dependabot should come from security_alerts, not {path}")

    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = _request
        resp = connected_client.get("/me/analytics/security-matrix/acme/api", headers={"X-GitHub-Token": "ghp_test"})

    assert resp.status_code == 200
    assert resp.json()["dependabot_critical_count"] == 1
    assert resp.json()["alerts_source"] == "aggregate"


def test_repo_security_row_github_error_maps_to_http_error(client):
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = _branch_error(404)
        resp = client.get("/me/analytics/security-matrix/acme/missing", headers={"X-GitHub-Token": "ghp_test"})
    assert resp.status_code == 400


def test_repo_security_row_github_unreachable_maps_to_503(client):
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.side_effect = httpx.ConnectError("down")
        resp = client.get("/me/analytics/security-matrix/acme/api", headers={"X-GitHub-Token": "ghp_test"})
    assert resp.status_code == 503


def test_repo_security_row_rejects_path_like_names_and_odd_github_metadata(client):
    assert client.get("/me/analytics/security-matrix/acme/a%20b", headers={"X-GitHub-Token": "ghp_test"}).status_code == 422
    assert client.get("/me/analytics/security-matrix/acme/a%3Fb", headers={"X-GitHub-Token": "ghp_test"}).status_code == 422
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request.return_value = ["not", "a", "repo"]
        resp = client.get("/me/analytics/security-matrix/acme/api", headers={"X-GitHub-Token": "ghp_test"})
    assert resp.status_code == 502


def _protection_stub(protection, rules=()):
    def _request(method, path, params=None):
        if path.endswith("/branches/main/protection"):
            if isinstance(protection, Exception):
                raise protection
            return protection
        if path.endswith("/branches/main"):
            return {"protected": True, "protection": {"enabled": True}}
        if path.endswith("/dependabot/alerts") or path.endswith("/code-scanning/alerts"):
            return []
        return {}

    return _request


@pytest.mark.parametrize(
    "protection, rules, allowed, unknown",
    [
        ({"allow_force_pushes": {"enabled": True}}, [], True, False),
        ({"allow_force_pushes": {"enabled": False}}, [], False, False),
        ({"allow_force_pushes": {"enabled": True}}, [{"type": "non_fast_forward"}], False, False),
        (_branch_error(404), [], True, False),  # no classic protection: force pushes allowed
        (_branch_error(404), [{"type": "non_fast_forward"}], False, False),  # a ruleset can still block
        (_branch_error(403), [], False, True),
        (httpx.RequestError("boom"), [], False, True),
    ],
)
def test_security_matrix_force_push_reads_the_protection_endpoint_and_rulesets(client, protection, rules, allowed, unknown):
    """The branch payload never carries allow_force_pushes, so the dimension comes from /protection + rulesets."""
    with patch("src.routers.security.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = lambda path, params=None, items_key=None: (
            [{"name": "api", "default_branch": "main"}] if path.endswith("/repos") or "/orgs/" in path or path.startswith("/user") else rules
        )
        mock_client.return_value.request.side_effect = _protection_stub(protection)
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["force_push_allowed"] is allowed
    assert ("force_push" in row["unknown_dimensions"]) is unknown


def test_security_matrix_counts_dependabot_alerts_beyond_the_first_page(client):
    """Alerts come from request_paginated (every page), not one 30-alert request: a critical alert far
    down the list must still be counted."""
    alerts = [{"security_advisory": {"severity": "low"}}] * 44 + [{"security_advisory": {"severity": "critical"}}]
    seen = []

    def _request(method, path, params=None):
        seen.append(path)
        if path.endswith("/branches/main"):
            return {"protected": True}
        return {}

    with patch("src.routers.security.GitHubClient") as mock_client:
        inst = mock_client.return_value

        def paginated(path, params=None, items_key=None):
            if path.endswith("/dependabot/alerts"):
                return alerts
            return [{"name": "api", "default_branch": "main", "security_and_analysis": {}}]

        inst.request_paginated.side_effect = paginated
        inst.request.side_effect = _request
        resp = client.get("/me/analytics/security-matrix/acme", headers={"X-GitHub-Token": "ghp_test"})

    row = resp.json()["repos"][0]
    assert row["dependabot_critical_count"] == 1
    assert not any(p.endswith("/dependabot/alerts") for p in seen)
