"""SLA-breach notifications (#834): the sweep, the destination event, the feed item and the digest section."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.core.db import SecurityAlert
from src.repositories import notification_repo, org_repo
from src.routers.org_notifications import _sla_breach_items
from src.services import digest_service, notifications
from src.services.security_alert_sweep import run_security_alert_sweep

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def org(db):
    return org_repo.get_or_create(db, github_login="acme")


def _alert(db, org, number, *, age_days, severity="critical", kind="dependabot", state="open"):
    created = NOW - timedelta(days=age_days)
    db.add(
        SecurityAlert(
            tenant_id=org.tenant_id, repo="api", kind=kind, number=number, state=state, severity=severity,
            created_at=created, updated_at=created,
            # Not the new-critical sweep's business here: pretend it already announced these.
            alert_notified_at=NOW,
        )
    )
    db.commit()


def _seed(db, org):
    _alert(db, org, 1, age_days=8)  # critical, 7-day SLA: breached
    _alert(db, org, 2, age_days=3)  # critical, inside the window
    _alert(db, org, 3, age_days=31, severity="high")  # high, 30-day SLA: breached
    _alert(db, org, 4, age_days=10, severity="high")  # inside
    _alert(db, org, 5, age_days=200, severity="medium")  # no SLA for medium
    _alert(db, org, 6, age_days=30, state="dismissed")  # closed
    _alert(db, org, 7, age_days=30, kind="code_scanning")  # not a Dependabot alert


def test_sweep_announces_each_breach_once_and_stamps_it(db, org):
    _seed(db, org)
    with patch("src.services.sla_breach_sweep.notifications.notify_sla_breaches") as notify:
        run_security_alert_sweep(db)
        run_security_alert_sweep(db)  # second tick: already stamped
    assert notify.call_count == 1
    announced = notify.call_args.args[3]
    assert sorted(b["number"] for b in announced) == [1, 3]
    assert {b["number"]: (b["severity"], b["sla_days"]) for b in announced} == {1: ("critical", 7), 3: ("high", 30)}
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    stamped = db.execute(
        text("SELECT number FROM security_alerts WHERE tenant_id = :t AND sla_notified_at IS NOT NULL ORDER BY number"),
        {"t": org.tenant_id},
    ).scalars().all()
    assert stamped == [1, 3]


def test_a_failing_delivery_neither_stops_the_sweep_nor_repeats(db, org):
    _alert(db, org, 1, age_days=8)
    with patch("src.services.sla_breach_sweep.notifications.notify_sla_breaches", side_effect=RuntimeError("down")) as notify:
        run_security_alert_sweep(db)
        run_security_alert_sweep(db)
    assert notify.call_count == 1  # stamped before sending: a failed message is not retried every tick


def test_only_subscribed_destinations_get_the_message(db, org):
    hit = notification_repo.create(
        db, tenant_id=org.tenant_id, kind="slack", name="n", encrypted_url="v2:x", events=["sla_breach"], enabled=True
    )
    notification_repo.create(
        db, tenant_id=org.tenant_id, kind="slack", name="m", encrypted_url="v2:x", events=["score_drop"], enabled=True
    )
    breaches = [{"repo": "api", "number": 1, "severity": "critical", "sla_days": 7}]
    with patch("src.services.notifications.send", return_value=(True, "HTTP 200")) as send:
        notifications.notify_sla_breaches(db, org.tenant_id, "acme", [])
        send.assert_not_called()
        notifications.notify_sla_breaches(db, org.tenant_id, "acme", breaches)
    assert [c.args[0].id for c in send.call_args_list] == [hit.id]
    assert "passed the remediation SLA" in send.call_args.args[2]


def test_feed_item_is_dated_by_the_breach_moment_and_respects_the_window(db, org):
    _seed(db, org)
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    items = _sla_breach_items(db, org.tenant_id, NOW - timedelta(days=14), 40)
    assert sorted(i["id"] for i in items) == sorted(
        f"sla_breach:{r.id}" for r in db.query(SecurityAlert).filter(SecurityAlert.number.in_([1, 3])).all()
    )
    by_title = {i["title"]: i for i in items}
    critical = next(i for i in items if i["title"].startswith("Critical"))
    assert abs((critical["at"] - (NOW - timedelta(days=1))).total_seconds()) < 5  # opened 8d ago + 7d window
    assert critical["href"] == "/security/burndown?repo=api"
    assert len(by_title) == 2
    # A breach older than the window is history, not a notification.
    assert _sla_breach_items(db, org.tenant_id, NOW, 40) == []


def test_digest_lists_breached_alerts(db, org):
    _seed(db, org)
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    content = digest_service.build_digest(db, tenant_id=org.tenant_id, org_login="acme", period_label="weekly")
    assert content.sla_breach_total == 2
    body = digest_service.render_text(content)
    assert "Dependabot alerts past their SLA (2)" in body
    assert "api #3 (high" in body
