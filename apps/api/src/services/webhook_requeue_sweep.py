"""Issue #409: periodically re-XADD webhook_deliveries rows stuck at status='queue_failed'.
Issue #440: also re-XADD a row still at status='queued' well past normal processing time --
the recovery path for one whose stream entry was trimmed (see routers/webhooks.py's
_WEBHOOK_STREAM_MAXLEN) before the consumer group ever read it, not just for a failed XADD.

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

# Must match routers/webhooks.py's own _WEBHOOK_STREAM_KEY/_WEBHOOK_STREAM_MAXLEN --
# duplicated constants, same reasoning as that module's note on why (independently
# deployable services, no shared code).
_STREAM_KEY = "webhook_events"
_STREAM_MAXLEN = 50_000
# A row this old is treated as permanently lost rather than retried forever -- an outage
# lasting longer than this needs an operator, not an indefinite silent background retry.
_MAX_AGE_HOURS = 24
# issue #440: a row still 'queued' this long after receipt is well past any normal
# XREADGROUP pickup latency (seconds) -- either its stream entry was trimmed by
# _WEBHOOK_STREAM_MAXLEN before the consumer group read it, or the original XADD's
# success was never actually durable. Re-XADD is safe either way: the consumer only
# marks a row 'processed' after it's actually applied, so a row still 'queued' has
# definitely not been consumed yet, and re-adding it can't cause a double-apply.
#
# Excludes rows event_consumer.py deliberately leaves 'queued' forever on purpose (not
# stuck): a null tenant_id (no tenant to scope a normalized row to) or an event_type with
# no normalizer yet (membership/team, see event_consumer.py's
# _NOT_YET_NORMALIZED_EVENT_TYPES). Without this exclusion, this sweep would re-XADD
# those every tick forever -- the consumer re-acks without ever changing their status, so
# they'd cross this threshold again next tick and repeat indefinitely, defeating the
# whole point of bounding the stream.
_STUCK_QUEUED_MINUTES = 30
_NOT_YET_NORMALIZED_EVENT_TYPES = ("membership", "team")
# Cap per tick so a large backlog can't block the loop indefinitely; the next tick picks
# up whatever's left.
_BATCH_LIMIT = 200


def run_webhook_requeue_sweep(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_HOURS)
    stuck_queued_cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STUCK_QUEUED_MINUTES)
    # FOR UPDATE SKIP LOCKED so concurrent API replicas each claim a disjoint set of rows
    # instead of racing to re-XADD (and double-enqueue) the same one -- same idiom
    # apps/worker/src/worker.py's own job-queue poll uses for the same reason. Held for the
    # whole batch (single commit at the end), not released mid-loop.
    rows = db.execute(
        text(
            "SELECT id, tenant_id, event_type, received_at, status FROM webhook_deliveries "
            "WHERE status = 'queue_failed' OR ("
            "  status = 'queued' AND received_at < :stuck_queued_cutoff"
            "  AND tenant_id IS NOT NULL AND event_type != ALL(:not_yet_normalized)"
            ") "
            "ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED"
        ),
        {
            "limit": _BATCH_LIMIT,
            "stuck_queued_cutoff": stuck_queued_cutoff,
            "not_yet_normalized": list(_NOT_YET_NORMALIZED_EVENT_TYPES),
        },
    ).fetchall()
    if not rows:
        return

    # The 24h abandon cutoff only ever applies to 'queue_failed' rows -- a stuck 'queued'
    # row hasn't failed anything, it just needs another XADD, so it's always retryable
    # here regardless of age.
    stale_ids = [row.id for row in rows if row.status == "queue_failed" and row.received_at < cutoff]
    retryable = [row for row in rows if row.id not in stale_ids]

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
        logger.warning("webhook requeue sweep retrying %d row(s) stuck at queue_failed or queued", len(retryable))
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
                    maxlen=_STREAM_MAXLEN,
                    approximate=True,
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
