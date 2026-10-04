"""Tests for the Dependabot burn-down (service math + org endpoint)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import SecurityAlert, User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.security import router as security_router
from src.services import dependabot_burndown as bd

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SLA = {"critical": 7, "high": 30}


def _ago(days):
    return NOW - timedelta(days=days)


def _row(repo="acme/api", number=1, state="open", severity="critical", created=10, updated=None):
    return SimpleNamespace(
        repo=repo,
        number=number,
        state=state,
        severity=severity,
        created_at=_ago(created),
        updated_at=_ago(created if updated is None else updated),
    )


def test_summary_median_oldest_and_sla_breaches():
    rows = [
        _row(number=1, severity="critical", created=10),  # breaches 7d
        _row(number=2, severity="critical", created=2),
        _row(number=3, severity="critical", created=4),
        _row(number=4, severity="high", created=20),  # within 30d
        _row(number=5, severity="critical", state="fixed", created=50, updated=3),  # closed: ignored
        _row(number=6, severity="low", created=400),  # no SLA for low
    ]
    summary = {s["severity"]: s for s in bd.summarize(rows, NOW, SLA)}

    assert summary["critical"] == {
        "severity": "critical", "open": 3, "median_age_days": 4.0, "oldest_age_days": 10.0, "sla_days": 7, "breaches": 1,
    }
    assert summary["high"]["breaches"] == 0 and summary["high"]["open"] == 1
    assert summary["low"]["sla_days"] is None and summary["low"]["breaches"] == 0
    assert summary["medium"]["open"] == 0 and summary["medium"]["median_age_days"] is None


def test_by_repo_orders_breaching_repos_first_and_links_the_breaching_alert():
    rows = [
        _row(repo="acme/ok", number=1, severity="high", created=40 - 25),  # 15d, within SLA
        _row(repo="acme/bad", number=2, severity="critical", created=3),
        _row(repo="acme/bad", number=3, severity="critical", created=9),  # breaches
        _row(repo="acme/bad", number=4, severity="high", created=35),  # breaches, oldest
        _row(repo="acme/closed", number=5, state="dismissed", created=100),
    ]
    out = bd.by_repo(rows, NOW, SLA)

    assert [r["repo"] for r in out] == ["acme/bad", "acme/ok"]
    bad = out[0]
    assert bad["breaches"] == 2 and bad["oldest_alert_number"] == 4 and bad["oldest_age_days"] == 35.0
    assert bad["open"] == {"critical": 2, "high": 1, "medium": 0, "low": 0}


def test_trend_counts_alerts_open_at_the_end_of_each_day():
    rows = [
        _row(number=1, severity="critical", created=5, state="fixed", updated=2),  # open days -5..-2 (closed on -2)
        _row(number=2, severity="critical", created=1),  # open since yesterday
    ]
    points = {p["date"]: p for p in bd.trend(rows, NOW.date(), 7)}

    assert points[(NOW.date() - timedelta(days=6)).isoformat()]["critical"] == 0
    assert points[(NOW.date() - timedelta(days=4)).isoformat()]["critical"] == 1
    assert points[(NOW.date() - timedelta(days=2)).isoformat()]["critical"] == 0  # closed that day
    assert points[(NOW.date() - timedelta(days=1)).isoformat()]["critical"] == 1
    assert points[NOW.date().isoformat()]["critical"] == 1
    assert len(points) == 7


def test_compute_clamps_the_trend_window():
    assert bd.compute([], NOW, 0, SLA)["window_days"] == 1
    assert len(bd.compute([], NOW, 10_000, SLA)["trend"]) == bd.TREND_MAX_DAYS


# --- endpoint ---

_USER = UserOut(id=777001, email="burn@example.com", name=None, is_workspace_admin=False)


@pytest.fixture()
def acme(db):
    db.add(User(id=_USER.id, email=_USER.email, name=None, password_hash=None, is_workspace_admin=False))
    db.commit()
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=_USER.id, role="member")
    return org


def _client(db, user=_USER):
    app = FastAPI()
    app.include_router(security_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _alert(db, org, number, kind="dependabot", **kw):
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    now = datetime.now(timezone.utc)
    db.add(
        SecurityAlert(
            tenant_id=org.tenant_id,
            repo=kw.get("repo", "acme/api"),
            kind=kind,
            number=number,
            state=kw.get("state", "open"),
            severity=kw.get("severity", "critical"),
            details={},
            created_at=now - timedelta(days=kw.get("age", 10)),
            updated_at=now,
        )
    )
    db.commit()


def test_endpoint_reports_only_this_orgs_dependabot_alerts(db, acme):
    other = org_repo.get_or_create(db, github_login="other")
    _alert(db, acme, 1, age=10)
    _alert(db, acme, 2, kind="code_scanning", age=10)  # not dependabot
    _alert(db, other, 3, age=10)  # another tenant
    db.execute(text(f"SET app.tenant_id = {int(acme.tenant_id)}"))

    resp = _client(db).get("/orgs/acme/security/dependabot-burndown?days=7")

    assert resp.status_code == 200
    body = resp.json()
    critical = next(s for s in body["severities"] if s["severity"] == "critical")
    assert critical["open"] == 1 and critical["breaches"] == 1 and critical["sla_days"] == 7
    assert [r["repo"] for r in body["repos"]] == ["acme/api"]
    assert len(body["trend"]) == 7


def test_endpoint_uses_configured_sla_and_falls_back_on_bad_values(db, acme):
    _alert(db, acme, 1, age=10)
    db.execute(text(f"SET app.tenant_id = {int(acme.tenant_id)}"))
    client = _client(db)

    def critical(values):
        with patch("src.routers.security.get_config", side_effect=lambda k, d: values.get(k, d)):
            body = client.get("/orgs/acme/security/dependabot-burndown").json()
        return next(s for s in body["severities"] if s["severity"] == "critical")

    assert critical({"dependabot_sla_critical_days": "14"})["breaches"] == 0
    assert critical({"dependabot_sla_critical_days": "not-a-number"})["sla_days"] == 7
    assert critical({"dependabot_sla_critical_days": "0"})["sla_days"] == 7


def test_endpoint_rejects_non_members(db, acme):
    outsider = UserOut(id=777002, email="outsider-burn@example.com", name=None, is_workspace_admin=False)
    db.add(User(id=outsider.id, email=outsider.email, name=None, password_hash=None, is_workspace_admin=False))
    db.commit()
    assert _client(db, outsider).get("/orgs/acme/security/dependabot-burndown").status_code in (403, 404)
