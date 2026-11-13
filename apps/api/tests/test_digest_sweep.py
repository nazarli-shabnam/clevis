"""Tests for the leadership-digest sweep."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from src.core.db import User
from src.repositories import org_repo, scan_results_repo, tenant_repo
from src.services import digest_sweep
from src.services.digest_sweep import run_digest_sweep


def _admin(db, tenant_id, email, verified=True):
    user = User(email=email, name=None, password_hash=None, email_verified=verified)
    db.add(user)
    db.commit()
    db.refresh(user)
    tenant_repo.get_or_create_membership(db, tenant_id=tenant_id, user_id=user.id, role="admin")
    db.commit()
    return user


def _seed_org_with_scan(db, login):
    org = org_repo.get_or_create(db, github_login=login)
    scan_results_repo.insert(
        db, owner=login, score=80, total_checks=2, failed_checks=1,
        checks=[{"id": "c", "title": "Broken thing", "severity": "high", "status": "fail"}],
        tenant_id=org.tenant_id,
    )
    return org


def _run(db, cadence="weekly", configured=True):
    with (
        patch("src.services.digest_sweep.get_config", return_value=cadence),
        patch("src.services.digest_sweep.email.is_configured", return_value=configured),
        patch("src.services.digest_sweep.email.send_email") as send,
    ):
        run_digest_sweep(db)
    return send


def _audit_rows(db, tenant_id):
    db.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))
    return db.execute(
        text("SELECT COUNT(*) FROM audit_logs WHERE tenant_id = :t AND action = 'digest.sent'"),
        {"t": tenant_id},
    ).scalar()


def test_cadence_off_is_a_noop(db):
    org = _seed_org_with_scan(db, "digest-sweep-off")
    _admin(db, org.tenant_id, "off-admin@e.com")
    send = _run(db, cadence="off")
    send.assert_not_called()
    assert _audit_rows(db, org.tenant_id) == 0


def test_unconfigured_smtp_is_a_noop(db):
    org = _seed_org_with_scan(db, "digest-sweep-nosmtp")
    _admin(db, org.tenant_id, "nosmtp-admin@e.com")
    send = _run(db, configured=False)
    send.assert_not_called()
    assert _audit_rows(db, org.tenant_id) == 0


def test_due_org_emails_verified_admins_and_records_an_audit_log(db):
    org = _seed_org_with_scan(db, "digest-sweep-due")
    _admin(db, org.tenant_id, "due-admin@e.com")
    _admin(db, org.tenant_id, "unverified@e.com", verified=False)

    send = _run(db)

    assert send.call_count == 1
    assert send.call_args[0][0] == "due-admin@e.com"
    assert "digest-sweep-due" in send.call_args[0][1]
    assert _audit_rows(db, org.tenant_id) == 1


def test_recently_sent_org_is_skipped(db):
    org = _seed_org_with_scan(db, "digest-sweep-recent")
    _admin(db, org.tenant_id, "recent-admin@e.com")
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    db.execute(
        text(
            "INSERT INTO audit_logs (actor, action, target, payload, tenant_id, created_at) "
            "VALUES ('system:digest', 'digest.sent', 'digest-sweep-recent', '{}', :t, :ts)"
        ),
        {"t": org.tenant_id, "ts": datetime.now(timezone.utc) - timedelta(days=2)},
    )
    db.commit()

    send = _run(db)
    send.assert_not_called()


def test_due_state_is_rechecked_after_acquiring_the_slot(db):
    # A peer replica can send and commit its digest.sent row between this sweep's
    # tenant query and slot acquisition; the recheck after try_acquire_sweep_slot
    # must catch that and not send a duplicate.
    org = _seed_org_with_scan(db, "digest-sweep-race")
    _admin(db, org.tenant_id, "race-admin@e.com")

    real_acquire = digest_sweep.try_acquire_sweep_slot

    def acquire_then_simulate_peer(session, key, tenant_id):
        got = real_acquire(session, key, tenant_id)
        if got:
            session.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))
            session.execute(
                text(
                    "INSERT INTO audit_logs (actor, action, target, payload, tenant_id, created_at) "
                    "VALUES ('system:digest', 'digest.sent', 'peer', '{}', :t, now())"
                ),
                {"t": tenant_id},
            )
        return got

    with (
        patch("src.services.digest_sweep.get_config", return_value="weekly"),
        patch("src.services.digest_sweep.email.is_configured", return_value=True),
        patch("src.services.digest_sweep.email.send_email") as send,
        patch("src.services.digest_sweep.try_acquire_sweep_slot", side_effect=acquire_then_simulate_peer),
    ):
        run_digest_sweep(db)

    send.assert_not_called()
    assert _audit_rows(db, org.tenant_id) == 1  # only the peer's row, no second send


def test_org_with_no_verified_admin_is_skipped(db):
    org = _seed_org_with_scan(db, "digest-sweep-noadmin")
    _admin(db, org.tenant_id, "noadmin-unverified@e.com", verified=False)
    send = _run(db)
    send.assert_not_called()
    assert _audit_rows(db, org.tenant_id) == 0


def test_org_with_no_scan_history_is_skipped(db):
    org = org_repo.get_or_create(db, github_login="digest-sweep-noscan")
    _admin(db, org.tenant_id, "noscan-admin@e.com")
    send = _run(db)
    send.assert_not_called()
    assert _audit_rows(db, org.tenant_id) == 0


def _actions(db, tenant_id):
    db.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))
    return [
        r[0]
        for r in db.execute(
            text("SELECT action FROM audit_logs WHERE tenant_id = :t AND action LIKE 'digest.%' ORDER BY id"),
            {"t": tenant_id},
        )
    ]


def _run_with_sender(db, sender):
    with (
        patch("src.services.digest_sweep.get_config", return_value="weekly"),
        patch("src.services.digest_sweep.email.is_configured", return_value=True),
        patch("src.services.digest_sweep.email.send_email", side_effect=sender) as send,
    ):
        run_digest_sweep(db)
    return send


def test_a_partial_smtp_failure_is_retried_for_the_failed_recipient_only(db):
    org = _seed_org_with_scan(db, "digest-sweep-partial")
    _admin(db, org.tenant_id, "ok-admin@e.com")
    _admin(db, org.tenant_id, "flaky-admin@e.com")
    flaky = {"down": True}

    def sender(address, subject, body):
        if address == "flaky-admin@e.com" and flaky["down"]:
            raise RuntimeError("smtp blip")

    first = _run_with_sender(db, sender)
    assert sorted(c.args[0] for c in first.call_args_list) == ["flaky-admin@e.com", "ok-admin@e.com"]
    assert "digest.sent" not in _actions(db, org.tenant_id)  # the period stays open

    flaky["down"] = False
    second = _run_with_sender(db, sender)
    assert [c.args[0] for c in second.call_args_list] == ["flaky-admin@e.com"]  # nobody is emailed twice
    assert _actions(db, org.tenant_id).count("digest.sent") == 1

    third = _run_with_sender(db, sender)
    third.assert_not_called()  # the period is closed


def test_a_recipient_that_keeps_failing_is_given_up_on_for_the_period(db):
    org = _seed_org_with_scan(db, "digest-sweep-giveup")
    _admin(db, org.tenant_id, "bad-mailbox@e.com")

    def sender(address, subject, body):
        raise RuntimeError("550 no such mailbox")

    for attempt in range(1, digest_sweep.MAX_ATTEMPTS_PER_RECIPIENT + 1):
        assert _run_with_sender(db, sender).call_count == 1
        actions = _actions(db, org.tenant_id)
        assert actions.count("digest.recipient_failed") == attempt
        # The give-up marker appears with the last failed attempt, not earlier.
        assert ("digest.sent" in actions) == (attempt == digest_sweep.MAX_ATTEMPTS_PER_RECIPIENT)

    _run_with_sender(db, sender).assert_not_called()


def test_is_due_treats_a_naive_timestamp_as_utc():
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    assert digest_sweep._is_due(datetime(2026, 11, 1), now, 7)
    assert not digest_sweep._is_due(datetime(2026, 11, 11), now, 7)


def test_delivery_state_skips_audit_rows_with_an_unreadable_payload(db):
    org = _seed_org_with_scan(db, "digest-sweep-badpayload")
    db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
    for payload in ("not json", '{"to": "Ok@E.com"}'):
        db.execute(
            text(
                "INSERT INTO audit_logs (actor, action, target, payload, tenant_id, created_at) "
                "VALUES ('system:digest', 'digest.recipient_sent', 't', :p, :t, now())"
            ),
            {"p": payload, "t": org.tenant_id},
        )

    done, failures = digest_sweep._delivery_state(db, org.tenant_id, None)

    assert done == {"ok@e.com"}
    assert failures == {}


def test_delivery_stops_when_a_peer_closes_the_period_midway(db):
    org = _seed_org_with_scan(db, "digest-sweep-peerclose")
    _admin(db, org.tenant_id, "a-admin@e.com")
    _admin(db, org.tenant_id, "b-admin@e.com")

    def sender(address, subject, body):
        # A peer replica finishes the period while this one is partway through the recipient list.
        db.execute(text(f"SET app.tenant_id = {int(org.tenant_id)}"))
        db.execute(
            text(
                "INSERT INTO audit_logs (actor, action, target, payload, tenant_id, created_at) "
                "VALUES ('system:digest', 'digest.sent', 'peer', '{}', :t, now())"
            ),
            {"t": org.tenant_id},
        )

    send = _run_with_sender(db, sender)

    assert send.call_count == 1  # the second recipient is left to the peer
    assert _actions(db, org.tenant_id).count("digest.sent") == 1


def test_delivery_is_abandoned_when_the_slot_is_lost_between_recipients(db):
    org = _seed_org_with_scan(db, "digest-sweep-slotlost")
    _admin(db, org.tenant_id, "a-admin@e.com")
    _admin(db, org.tenant_id, "b-admin@e.com")
    real_acquire = digest_sweep.try_acquire_sweep_slot
    calls = {"n": 0}

    def acquire_once(session, key, tenant_id):
        calls["n"] += 1
        return real_acquire(session, key, tenant_id) if calls["n"] == 1 else False

    with (
        patch("src.services.digest_sweep.get_config", return_value="weekly"),
        patch("src.services.digest_sweep.email.is_configured", return_value=True),
        patch("src.services.digest_sweep.email.send_email") as send,
        patch("src.services.digest_sweep.try_acquire_sweep_slot", side_effect=acquire_once),
    ):
        run_digest_sweep(db)

    assert send.call_count == 1  # a peer holds the slot now, so the rest is its job
    assert "digest.sent" not in _actions(db, org.tenant_id)
