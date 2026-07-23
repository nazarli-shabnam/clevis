"""Tests for the org-wide failed-runs and release-timeline routes (docs/plan.md Phase 17)."""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.github import router as github_router

_MEMBER = UserOut(id=1, email="member@example.com", name=None, is_workspace_admin=False)


@pytest.fixture()
def acme_org(db):
    user = User(id=_MEMBER.id, email=_MEMBER.email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role="member")
    return org


@pytest.fixture()
def client(db, acme_org):
    app = FastAPI()
    app.include_router(github_router)
    app.dependency_overrides[require_auth] = lambda: _MEMBER
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _run(id_, workflow_id, status, conclusion, created_at):
    return {
        "id": id_,
        "workflow_id": workflow_id,
        "name": "CI",
        "status": status,
        "conclusion": conclusion,
        "head_branch": "main",
        "created_at": created_at,
        "run_started_at": created_at,
        "updated_at": created_at,
        "html_url": f"https://github.com/acme/api/actions/runs/{id_}",
        "actor": {"login": "alice"},
    }


def test_failed_runs_requires_three_consecutive_failures(client):
    with patch("src.routers.github.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [{"name": "api"}]
        mock_client.return_value.request.return_value = {
            "workflow_runs": [
                _run(3, 1, "completed", "failure", "2026-07-20T00:00:00Z"),
                _run(2, 1, "completed", "failure", "2026-07-19T00:00:00Z"),
                _run(1, 1, "completed", "success", "2026-07-18T00:00:00Z"),
            ]
        }
        resp = client.post("/github/orgs/acme/failed-runs", json={"token": "ghp_testtoken123456789012345678901234"})

    assert resp.status_code == 200
    assert resp.json()["runs"] == []  # only 2 consecutive failures, below the threshold of 3


def test_failed_runs_dedups_to_one_entry_per_streak(client):
    with patch("src.routers.github.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [{"name": "api"}]
        mock_client.return_value.request.return_value = {
            "workflow_runs": [
                _run(4, 1, "completed", "failure", "2026-07-21T00:00:00Z"),
                _run(3, 1, "completed", "failure", "2026-07-20T00:00:00Z"),
                _run(2, 1, "completed", "failure", "2026-07-19T00:00:00Z"),
                _run(1, 1, "completed", "success", "2026-07-18T00:00:00Z"),
            ]
        }
        resp = client.post("/github/orgs/acme/failed-runs", json={"token": "ghp_testtoken123456789012345678901234"})

    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == 4
    assert runs[0]["consecutive_failures"] == 3


def test_failed_runs_one_bad_repo_does_not_blank_others(client):
    def _paginated_side_effect(path, params=None):
        return [{"name": "repo-bad"}, {"name": "repo-good"}]

    def _request_side_effect(method, path, params=None):
        if "repo-bad" in path:
            raise httpx.RequestError("boom")
        return {
            "workflow_runs": [
                _run(3, 1, "completed", "failure", "2026-07-20T00:00:00Z"),
                _run(2, 1, "completed", "failure", "2026-07-19T00:00:00Z"),
                _run(1, 1, "completed", "failure", "2026-07-18T00:00:00Z"),
            ]
        }

    with patch("src.routers.github.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.side_effect = _paginated_side_effect
        mock_client.return_value.request.side_effect = _request_side_effect
        resp = client.post("/github/orgs/acme/failed-runs", json={"token": "ghp_testtoken123456789012345678901234"})

    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["repo"] == "acme/repo-good"


def test_failed_runs_no_token_returns_400(client):
    resp = client.post("/github/orgs/acme/failed-runs", json={})
    assert resp.status_code == 400


def test_failed_runs_non_member_forbidden(db):
    org_repo.get_or_create(db, github_login="acme")
    app = FastAPI()
    app.include_router(github_router)
    app.dependency_overrides[require_auth] = lambda: UserOut(id=999, email="outsider@example.com", name=None, is_workspace_admin=False)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).post("/github/orgs/acme/failed-runs", json={})
    assert resp.status_code == 403


def test_release_timeline_filters_by_days_and_excludes_missing_published_at(client):
    with patch("src.routers.github.GitHubClient") as mock_client:
        mock_client.return_value.request_paginated.return_value = [{"name": "api"}]
        mock_client.return_value.request.return_value = [
            {
                "tag_name": "v1.0.0",
                "name": "v1.0.0",
                "published_at": "2026-07-20T00:00:00Z",
                "prerelease": False,
                "body": "x" * 200,
                "html_url": "https://github.com/acme/api/releases/v1.0.0",
            },
            {
                "tag_name": "v0.9.0",
                "name": "v0.9.0",
                "published_at": "2020-01-01T00:00:00Z",  # far older than the 90-day window
                "prerelease": False,
                "body": "old",
                "html_url": "https://github.com/acme/api/releases/v0.9.0",
            },
            {
                "tag_name": "draft",
                "published_at": None,  # unpublished draft, no real date
            },
        ]
        resp = client.post("/github/orgs/acme/release-timeline", json={"token": "ghp_testtoken123456789012345678901234", "days": 90})

    assert resp.status_code == 200
    releases = resp.json()["releases"]
    assert len(releases) == 1
    assert releases[0]["tag_name"] == "v1.0.0"
    assert len(releases[0]["body_preview"]) == 120


def test_release_timeline_no_token_returns_400(client):
    resp = client.post("/github/orgs/acme/release-timeline", json={})
    assert resp.status_code == 400
