"""Issue #409: periodically re-XADD webhook_deliveries rows stuck at status='queue_failed'.

A transient Redis blip at receive time (routers/webhooks.py's _handle_ingested_event)
durably stores the verified payload but never gets a second chance onto the stream
without this -- the row just sits there forever, silently absent from every downstream
pipeline. Mirrors gap_heal_sweep.py's shape: called from an asyncio background loop
(webhook_requeue_loop.py) started in main.py's lifespan.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

# Must match routers/webhooks.py's own _WEBHOOK_STREAM_KEY -- duplicated constant, same
# reasoning as that module's note on why (independently deployable services, no shared code).
_STREAM_KEY = "webhook_events"
# A row this old is treated as permanently lost rather than retried forever -- an outage
# lasting longer than this needs an operator, not an indefinite silent background retry.
_MAX_AGE_HOURS = 24
# Cap per tick so a large backlog can't block the loop indefinitely; the next tick picks
# up whatever's left.
_BATCH_LIMIT = 200


def run_webhook_requeue_sweep(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_HOURS)
    # FOR UPDATE SKIP LOCKED so concurrent API replicas each claim a disjoint set of rows
    # instead of racing to re-XADD (and double-enqueue) the same one -- same idiom
    # apps/worker/src/worker.py's own job-queue poll uses for the same reason. Held for the
    # whole batch (single commit at the end), not released mid-loop.
    rows = db.execute(
        text(
            "SELECT id, tenant_id, event_type, received_at FROM webhook_deliveries "
            "WHERE status = 'queue_failed' ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED"
        ),
        {"limit": _BATCH_LIMIT},
    ).fetchall()
    if not rows:
        return

    stale_ids = [row.id for row in rows if row.received_at < cutoff]
    retryable = [row for row in rows if row.received_at >= cutoff]

    if stale_ids:
        logger.error(
            "webhook requeue sweep abandoning %d row(s) older than %dh -- never made it onto "
            "the queue and won't be retried again: ids=%s",
            len(stale_ids), _MAX_AGE_HOURS, stale_ids,
        )
        db.execute(
            text("UPDATE webhook_deliveries SET status = 'queue_abandoned' WHERE id = ANY(:ids)"),
            {"ids": stale_ids},
        )

    if retryable:
        logger.warning("webhook requeue sweep retrying %d row(s) stuck at queue_failed", len(retryable))
        client = get_redis_client()
        requeued_ids = []
        for row in retryable:
            try:
                client.xadd(
                    _STREAM_KEY,
                    {
                        "delivery_row_id": row.id,
                        "event_type": row.event_type,
                        "tenant_id": row.tenant_id if row.tenant_id is not None else "",
                    },
                )
            except Exception:
                # Redis is still down (or newly down again) -- leave this and every
                # remaining row in this tick's batch at queue_failed rather than hammering
                # a dead Redis once per row; the next tick retries them all.
                logger.exception(
                    "webhook requeue sweep failed to re-enqueue row %d, aborting this tick's remaining retries", row.id
                )
                break
            requeued_ids.append(row.id)
        if requeued_ids:
            db.execute(
                text("UPDATE webhook_deliveries SET status = 'queued' WHERE id = ANY(:ids)"),
                {"ids": requeued_ids},
            )

    db.commit()
