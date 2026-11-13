"""Is GitHub data reaching Clevis? One read-only summary for an org admin.

Several views (activity by repo, the Dependabot and code-scanning columns, the member access review, the
burn-down) are filled from webhook deliveries, so "No events recorded" is ambiguous: a quiet org, or
broken ingestion. This answers it from rows that already exist (``webhook_deliveries``, the event
rollup and the sync cursors); nothing is stored for it."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.db import ActivitySyncCursor, OrgMembershipSyncCursor, RepoEventDailyCount, WebhookDelivery, get_db
from src.core.rbac import OrgContext, require_org_role

router = APIRouter()

_WINDOW = timedelta(hours=24)


class IngestionHealth(BaseModel):
    # Whether GitHub data ever reached this org: a stored delivery, or (once the retention sweep has purged a
    # quiet org's old deliveries) an event in the permanent daily rollup. False is the "webhook URL or secret
    # not configured?" case.
    has_deliveries: bool
    # Whether a webhook delivery row is stored for this org. False with has_deliveries true means the data
    # came from backfill, or the quiet org's old deliveries were purged: not evidence the webhook works.
    has_webhook_deliveries: bool
    # Newest delivery, else the start of the newest day with a recorded event (day precision, and it also
    # covers backfilled events, so it is a "data arrived" signal rather than strictly a webhook one).
    last_received_at: datetime | None
    # received_at of the newest delivery the worker has finished with (no separate processed time is kept).
    last_processed_at: datetime | None
    # Deliveries received in the last `window_hours`, by outcome.
    window_hours: int
    received: int
    queue_failed: int
    queue_abandoned: int
    dropped: int
    # Still waiting for the worker (status "queued").
    pending: int
    # Last successful event backfill / gap-heal, and membership reconcile.
    last_backfill_at: datetime | None
    last_membership_reconcile_at: datetime | None


@router.get("/orgs/{org_login}/ingestion-health", response_model=IngestionHealth)
def ingestion_health(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    tenant_id = ctx.org.tenant_id
    since = datetime.now(timezone.utc) - _WINDOW
    mine = WebhookDelivery.tenant_id == tenant_id

    last_received, total = db.query(func.max(WebhookDelivery.received_at), func.count()).filter(mine).one()
    if last_received is None:
        # Retention deletes processed deliveries after `retention_webhook_deliveries_days`, so a quiet org can
        # have none left although ingestion works; the daily rollup is never purged.
        last_day = db.query(func.max(RepoEventDailyCount.day)).filter(RepoEventDailyCount.tenant_id == tenant_id).scalar()
        if last_day is not None:
            last_received = datetime(last_day.year, last_day.month, last_day.day, tzinfo=timezone.utc)
    last_processed = (
        db.query(func.max(WebhookDelivery.received_at)).filter(mine, WebhookDelivery.status == "processed").scalar()
    )
    by_status = dict(
        db.query(WebhookDelivery.status, func.count())
        .filter(mine, WebhookDelivery.received_at >= since)
        .group_by(WebhookDelivery.status)
        .all()
    )
    backfill = db.query(ActivitySyncCursor.last_synced_at).filter(ActivitySyncCursor.tenant_id == tenant_id).scalar()
    reconcile = (
        db.query(OrgMembershipSyncCursor.last_synced_at).filter(OrgMembershipSyncCursor.tenant_id == tenant_id).scalar()
    )
    return IngestionHealth(
        has_deliveries=total > 0 or last_received is not None,
        has_webhook_deliveries=total > 0,
        last_received_at=last_received,
        last_processed_at=last_processed,
        window_hours=int(_WINDOW.total_seconds() // 3600),
        received=sum(by_status.values()),
        queue_failed=by_status.get("queue_failed", 0),
        queue_abandoned=by_status.get("queue_abandoned", 0),
        dropped=by_status.get("dropped", 0),
        pending=by_status.get("queued", 0),
        last_backfill_at=backfill,
        last_membership_reconcile_at=reconcile,
    )
