"""Tests for the webhook_deliveries re-enqueue sweep (issue #409)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.core.db import User, WebhookDelivery
from src.repositories import tenant_repo
from src.services.webhook_requeue_sweep import run_webhook_requeue_sweep


def _make_tenant(db, email: str) -> int:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return tenant_repo.ensure_personal_tenant(db, user.id).id


def _make_row(db, *, status="queue_failed", received_at=None, event_type="push", tenant_id=None) -> WebhookDelivery:
    row = WebhookDelivery(
        tenant_id=tenant_id,
        delivery_id="d1",
        event_type=event_type,
        installation_id=None,
        payload=b"{}",
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if received_at is not None:
        db.execute(
            __import__("sqlalchemy").text("UPDATE webhook_deliveries SET received_at = :ts WHERE id = :id"),
            {"ts": received_at, "id": row.id},
        )
        db.commit()
        db.refresh(row)
    return row


def test_sweep_is_a_noop_when_nothing_is_stuck(db):
    with patch("src.services.webhook_requeue_sweep.get_redis_client") as mock_get_client:
        run_webhook_requeue_sweep(db)
    mock_get_client.assert_not_called()


def test_sweep_requeues_a_recent_queue_failed_row(db):
    row = _make_row(db, tenant_id=None)

    mock_client = MagicMock()
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    mock_client.xadd.assert_called_once_with(
        "webhook_events",
        {"delivery_row_id": row.id, "event_type": "push", "tenant_id": ""},
        maxlen=50_000,
        approximate=True,
    )
    db.refresh(row)
    assert row.status == "queued"


def test_sweep_leaves_row_at_queue_failed_when_xadd_still_fails(db):
    # Regression test for issue #409's whole premise: before this sweep existed, a row
    # stuck here was never retried at all.
    row = _make_row(db)

    mock_client = MagicMock()
    mock_client.xadd.side_effect = ConnectionError("redis still down")
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    db.refresh(row)
    assert row.status == "queue_failed"


def test_sweep_stops_retrying_the_batch_after_the_first_xadd_failure(db):
    # If Redis is down, every remaining XADD in this tick's batch would fail the same
    # way -- don't hammer it once per row.
    row1 = _make_row(db)
    row2 = _make_row(db)

    mock_client = MagicMock()
    mock_client.xadd.side_effect = ConnectionError("redis still down")
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    assert mock_client.xadd.call_count == 1
    db.refresh(row1)
    db.refresh(row2)
    assert row1.status == "queue_failed"
    assert row2.status == "queue_failed"


def test_sweep_abandons_a_row_older_than_the_max_retry_age(db):
    stale = datetime.now(timezone.utc) - timedelta(hours=48)
    row = _make_row(db, received_at=stale)

    mock_client = MagicMock()
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    mock_client.xadd.assert_not_called()
    db.refresh(row)
    assert row.status == "queue_abandoned"


def test_sweep_does_not_touch_rows_already_queued_or_abandoned(db):
    queued_row = _make_row(db, status="queued")
    abandoned_row = _make_row(db, status="queue_abandoned")

    mock_client = MagicMock()
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    mock_client.xadd.assert_not_called()
    db.refresh(queued_row)
    db.refresh(abandoned_row)
    assert queued_row.status == "queued"
    assert abandoned_row.status == "queue_abandoned"


def test_sweep_reenqueues_a_queued_row_stuck_past_the_stuck_threshold(db):
    # Issue #440: a row still 'queued' this long after receipt almost certainly had its
    # stream entry trimmed (MAXLEN) before the consumer group ever read it -- the same
    # recovery path as a failed XADD, just a different original cause.
    tenant_id = _make_tenant(db, "webhook-sweep-stuck-queued@example.com")
    stuck = datetime.now(timezone.utc) - timedelta(minutes=45)
    row = _make_row(db, status="queued", received_at=stuck, tenant_id=tenant_id)

    mock_client = MagicMock()
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    mock_client.xadd.assert_called_once_with(
        "webhook_events",
        {"delivery_row_id": row.id, "event_type": "push", "tenant_id": tenant_id},
        maxlen=50_000,
        approximate=True,
    )
    db.refresh(row)
    assert row.status == "queued"


def test_sweep_does_not_reenqueue_a_stuck_queued_row_with_no_resolved_tenant(db):
    # event_consumer.py deliberately leaves a null-tenant_id row 'queued' forever (no
    # tenant to scope a normalized row to) -- re-XADDing it every tick would just repeat
    # forever, since the consumer re-acks it without ever changing its status.
    stuck = datetime.now(timezone.utc) - timedelta(minutes=45)
    row = _make_row(db, status="queued", received_at=stuck, tenant_id=None)

    mock_client = MagicMock()
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    mock_client.xadd.assert_not_called()
    db.refresh(row)
    assert row.status == "queued"


def test_sweep_does_not_reenqueue_a_stuck_queued_row_with_no_normalizer_yet(db):
    # membership/team events have no normalizer (event_consumer.py's
    # _NOT_YET_NORMALIZED_EVENT_TYPES) and are deliberately left 'queued' forever, same
    # reasoning as the null-tenant case above.
    tenant_id = _make_tenant(db, "webhook-sweep-no-normalizer@example.com")
    stuck = datetime.now(timezone.utc) - timedelta(minutes=45)
    row = _make_row(db, status="queued", received_at=stuck, tenant_id=tenant_id, event_type="membership")

    mock_client = MagicMock()
    with patch("src.services.webhook_requeue_sweep.get_redis_client", return_value=mock_client):
        run_webhook_requeue_sweep(db)

    mock_client.xadd.assert_not_called()
    db.refresh(row)
    assert row.status == "queued"
