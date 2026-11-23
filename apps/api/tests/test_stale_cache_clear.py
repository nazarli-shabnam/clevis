"""Scheduled clearing of idle Actions caches (#1290): the deletion rule, the sweep and the org controls."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import automation_settings_repo, org_membership_repo, org_repo
from src.routers.automation_schedules import router as schedules_router
from src.services import stale_cache_clear
from src.services.automation_sweep import run_automation_sweep

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def _cache(cache_id, days_idle):
    return {"id": cache_id, "last_accessed_at": (NOW - timedelta(days=days_idle)).isoformat()}


class _FakeClient:
    """GitHub's cache list, oldest access first, with deletes that remove entries from it."""

    def __init__(self, caches, gone=()):
        self.caches, self.gone, self.deleted, self.list_params = list(caches), set(gone), [], []

    def request(self, method, path, params=None, json=None):
        if method == "GET":
            self.list_params.append(params)
            page, size = (params or {}).get("page", 1), (params or {}).get("per_page", 100)
            return {"actions_caches": list(self.caches[(page - 1) * size : page * size])}
        cache_id = int(path.rsplit("/", 1)[1])
        if cache_id in self.gone:
            self.caches = [c for c in self.caches if c["id"] != cache_id]
            response = MagicMock(status_code=404)
            raise httpx.HTTPStatusError("gone", request=MagicMock(), response=response)
        self.deleted.append(cache_id)
        self.caches = [c for c in self.caches if c["id"] != cache_id]
        return {}


def test_only_caches_idle_longer_than_the_threshold_are_deleted_oldest_first():
    client = _FakeClient([_cache(1, 30), _cache(2, 9), _cache(3, 2)])

    assert stale_cache_clear.clear_idle_caches(client, "acme", "api", 7, NOW) == 2
    assert client.deleted == [1, 2]
    assert client.list_params[0]["sort"] == "last_accessed_at" and client.list_params[0]["direction"] == "asc"


def test_an_eligible_cache_behind_a_full_page_of_unknown_last_use_is_still_found(monkeypatch):
    monkeypatch.setattr(stale_cache_clear, "_PAGE", 2)
    client = _FakeClient([{"id": 1}, {"id": 2}, _cache(3, 30), _cache(4, 1)])

    assert stale_cache_clear.clear_idle_caches(client, "acme", "api", 7, NOW) == 1
    assert client.deleted == [3]  # the unknown ones were skipped, the in-use one ended the read


def test_a_cache_already_gone_is_skipped_and_an_unknown_last_use_is_never_deleted():
    client = _FakeClient([_cache(1, 30), {"id": 2}, {"id": 3, "last_accessed_at": "not-a-date"}], gone={1})

    assert stale_cache_clear.clear_idle_caches(client, "acme", "api", 7, NOW) == 0
    assert client.deleted == []


def test_other_github_errors_propagate():
    client = MagicMock()
    client.request.side_effect = httpx.HTTPStatusError("boom", request=MagicMock(), response=MagicMock(status_code=500))
    with pytest.raises(httpx.HTTPStatusError):
        stale_cache_clear.clear_idle_caches(client, "acme", "api", 7, NOW)


@pytest.fixture()
def org(db):
    org = org_repo.get_or_create(db, github_login="acme")
    org.stale_cache_clear_schedule = True
    db.commit()
    automation_settings_repo.upsert(
        db, org.tenant_id, "acme/api", "stale_cache_clear", enabled=True, extra={"idle_days": 3}
    )
    automation_settings_repo.upsert(db, org.tenant_id, "acme/off", "stale_cache_clear", enabled=False)
    return org


def test_the_sweep_clears_idle_caches_of_enabled_repos_with_their_threshold_and_audits_it(db, org):
    config = {"stale_cache_clear_cadence": "off"}
    with (
        patch("src.services.automation_sweep.get_config", side_effect=lambda k, d="": config.get(k, d)),
        patch("src.services.automation_sweep._has_installation", return_value=True),
        patch("src.services.automation_sweep.resolve_org_token", return_value="itok") as token,
        patch("src.services.automation_sweep.GitHubClient"),
        patch("src.services.automation_sweep.stale_cache_clear.clear_idle_caches", return_value=4) as clear,
    ):
        run_automation_sweep(db)

    assert [c.args[1:4] for c in clear.call_args_list] == [("acme", "api", 3)]  # only the enabled repo
    assert all(c.kwargs["client_token"] is None for c in token.call_args_list)
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    rows = db.execute(
        text("SELECT action, actor, payload FROM audit_logs WHERE tenant_id = :t ORDER BY id"), {"t": org.tenant_id}
    ).fetchall()
    assert ("stale_cache_clear.scheduled", "system:stale_cache_clear") in [(r[0], r[1]) for r in rows]
    sweep = next(r for r in rows if r[0] == "stale_cache_clear.sweep")
    assert '"deleted": 4' in str(sweep[2])


def _user(db, email):
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, email_verified=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=None, is_workspace_admin=False)


def test_org_controls_save_the_schedule_and_each_repos_idle_days(db):
    org = org_repo.get_or_create(db, github_login="acme")
    admin = _user(db, "cache-admin@e.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    app = FastAPI()
    app.include_router(schedules_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: admin
    db.execute(text(f"SET app.user_id = {admin.id}"))
    client = TestClient(app)

    with patch("src.services.automation_sweep.get_config", return_value="off"):
        on = client.put("/orgs/acme/automation-schedules", json={"stale_cache_clear": True}).json()
        saved = client.put(
            "/orgs/acme/repos/acme/Api/automation/stale-cache-clear", json={"enabled": True, "idle_days": 14}
        )
        too_long = client.put(
            "/orgs/acme/repos/acme/api/automation/stale-cache-clear", json={"enabled": True, "idle_days": 365}
        )
        listed = client.get("/orgs/acme/automation-schedules").json()

    assert (on["stale_cache_clear"]["effective"], on["stale_cache_clear"]["cadence"]) == (True, "weekly")
    assert on["pr_nudges"]["enabled"] is None  # only the sent field changed
    assert saved.status_code == 200 and too_long.status_code == 422
    assert listed["stale_cache_repos"] == {"acme/api": 14}
