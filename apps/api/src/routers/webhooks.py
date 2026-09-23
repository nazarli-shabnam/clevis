"""GitHub App webhook receiver: POST /webhooks/github verifies X-Hub-Signature-256, keeps
github_installations in sync on installation lifecycle events, and durably queues a
bounded set of event types for apps/worker's event-processor fleet.
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import WebhookDelivery, get_db
from src.core.rate_limit import rate_limit
from src.core.redis_client import get_redis_client
from src.repositories import audit_repo, installation_repo

logger = logging.getLogger(__name__)

router = APIRouter()

# Two families of durably-queued event types (X-GitHub-Event header names, not GitHub
# Events API `type` strings, hence "create" not "CreateEvent"):
#   - Activity Feed events (push/pull_request/issues/release/create) -- normalized into
#     repo_events/repo_event_daily_counts.
#   - Security alert events (dependabot_alert/code_scanning_alert/secret_scanning_alert)
#     -- normalized into security_alerts. Requires the App's Dependabot/Code scanning/
#     Secret scanning alerts: read permissions -- see docs/self-hosting.md.
#   - Org membership / repo access events (member/organization) -- normalized into
#     org_members/repo_collaborators. Requires the App's Members: read permission.
# An ingested event accumulates in webhook_deliveries with status="queued" until its
# consumer exists -- that's the expected handoff state, not a bug.
#
# membership/team are deliberately NOT subscribed: team-based repo access normalization
# doesn't exist yet, and ingesting them anyway had no bound. Re-add once that consumer exists.
_INGESTED_EVENT_TYPES = {
    "push",
    "pull_request",
    "issues",
    "release",
    "create",
    "dependabot_alert",
    "code_scanning_alert",
    "secret_scanning_alert",
    "member",
    "organization",
}

# Redis Stream key the ingestion path XADDs onto; the worker's consumer group reads
# from this same key.
_WEBHOOK_STREAM_KEY = "webhook_events"

# Approximate cap so the stream can't grow unbounded if the consumer group ever falls
# behind or stops -- the webhook_deliveries row (not the stream entry) is the real
# source of truth; webhook_requeue_sweep.py's staleness check recovers a row trimmed
# before consumption. 50k is generous headroom; the recovery sweep protects against
# real data loss, not this trim.
_WEBHOOK_STREAM_MAXLEN = 50_000

# GitHub caps webhook deliveries around 25MB; refuse anything larger long before that
# so a body this endpoint (unauthenticated until the signature check passes) can't be
# used to exhaust memory. Enforced by counting streamed bytes, not Content-Length.
_MAX_BODY_BYTES = 5 * 1024 * 1024


async def _read_bounded_body(request: Request) -> bytes:
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    # Compare as bytes, not str — hmac.compare_digest raises TypeError on a str with
    # non-ASCII characters (attacker-controlled here), which would otherwise 500.
    return hmac.compare_digest(expected.encode(), provided.encode("utf-8", errors="replace"))


# Generous relative to the auth-endpoint default: GitHub's webhook deliveries for many
# installations can share the same egress IP, so a strict per-IP limit risks throttling.
@router.post("/webhooks/github", dependencies=[Depends(rate_limit(max_requests=120, window_seconds=60))])
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    secret = settings.github_app_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GitHub webhook secret not configured")

    raw_body = await _read_bounded_body(request)
    if not _verify_signature(raw_body, request.headers.get("X-Hub-Signature-256"), secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    if event == "installation" and payload.get("action") == "deleted":
        _handle_installation_deleted(db, payload)
    elif event == "installation" and payload.get("action") == "new_permissions_accepted":
        _handle_installation_permissions_accepted(db, payload)
    elif event == "installation" and payload.get("action") in {"suspend", "unsuspend"}:
        # Not yet acted on beyond logging (token_resolution already degrades gracefully
        # against a suspended install) -- kept visible in API logs rather than a silent 200.
        logger.info(
            "installation.%s webhook for installation %s (not acted on yet)",
            payload.get("action"),
            (payload.get("installation") or {}).get("id"),
        )
    # installation.created is deliberately a no-op: rows are only ever written to
    # github_installations by the authenticated sync endpoints, which independently
    # verify the installation_id genuinely belongs to the claimed account before
    # persisting. Auto-creating a row from this webhook would trust GitHub's payload
    # alone to decide which Clevis org/user it belongs to, with no ownership check.
    #
    # installation_repositories has nothing to sync yet -- github_installations tracks
    # the installation itself, not per-repo access. Accepted (200) so GitHub doesn't retry.
    elif event in _INGESTED_EVENT_TYPES:
        _handle_ingested_event(db, event, delivery_id, raw_body, payload)

    return {"ok": True}


def _resolve_event_installation_id(payload: dict) -> int | None:
    installation_id = (payload.get("installation") or {}).get("id")
    if not isinstance(installation_id, int) or isinstance(installation_id, bool):
        return None
    return installation_id


def _handle_ingested_event(db: Session, event: str, delivery_id: str, raw_body: bytes, payload: dict) -> None:
    installation_id = _resolve_event_installation_id(payload)

    tenant_id = None
    if installation_id is not None:
        # SECURITY DEFINER lookup -- this receiver never has an authenticated session,
        # so it never has app.tenant_id set to look this up the normal way.
        tenant_id = db.execute(
            text("SELECT resolve_installation_tenant_id(:installation_id)"), {"installation_id": installation_id}
        ).scalar()

    row = WebhookDelivery(
        tenant_id=tenant_id,
        delivery_id=delivery_id,
        event_type=event,
        installation_id=installation_id,
        payload=raw_body,
        status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    try:
        client = get_redis_client()
        client.xadd(
            _WEBHOOK_STREAM_KEY,
            {
                "delivery_row_id": row.id,
                "event_type": event,
                "tenant_id": tenant_id if tenant_id is not None else "",
            },
            maxlen=_WEBHOOK_STREAM_MAXLEN,
            approximate=True,
        )
    except Exception:
        # A transient Redis blip isn't GitHub's retry problem -- the payload is already
        # durably stored above. Mark it so the re-enqueue sweep can find it; still 200 to GitHub.
        logger.exception("Failed to enqueue webhook_deliveries row %s onto Redis stream %s", row.id, _WEBHOOK_STREAM_KEY)
        row.status = "queue_failed"
        db.commit()


def _handle_installation_permissions_accepted(db: Session, payload: dict) -> None:
    """An org owner approved the App's updated permission request on GitHub. The payload
    carries the full `installation` object including its now-current `permissions` dict —
    persist it so the "some automations need extra access" notice clears on its own."""
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if not isinstance(installation_id, int) or isinstance(installation_id, bool):
        logger.warning("installation.new_permissions_accepted has a missing/non-integer id: %r", installation_id)
        return
    permissions = installation.get("permissions")
    if not isinstance(permissions, dict):
        logger.warning("installation.new_permissions_accepted %s has no permissions object", installation_id)
        return

    updated, changed = installation_repo.update_permissions(db, installation_id=installation_id, permissions=permissions)
    # Only write an audit entry when permissions actually changed -- GitHub redelivers
    # webhooks on retry, and without this check a redelivery would write a duplicate audit
    # row for the same approval. permissions_synced_at is still bumped either way.
    if updated and changed:
        # update_permissions already set the session tenant context via the SECURITY
        # DEFINER resolver; re-read it here so the audit row is attributed under RLS.
        tenant_id = db.execute(
            text("SELECT resolve_installation_tenant_id(:iid)"), {"iid": installation_id}
        ).scalar()
        audit_repo.write(
            db,
            actor="github-webhook",
            action="installation.permissions_accepted",
            target=str(installation_id),
            payload={"installation_id": installation_id, "permissions": permissions},
            tenant_id=tenant_id,
        )


def _handle_installation_deleted(db: Session, payload: dict) -> None:
    installation_id = (payload.get("installation") or {}).get("id")
    if not isinstance(installation_id, int) or isinstance(installation_id, bool):
        logger.warning("installation.deleted webhook has a missing/non-integer installation.id: %r", installation_id)
        return
    removed, tenant_id = installation_repo.delete_by_installation_id(db, installation_id)
    if removed:
        audit_repo.write(
            db,
            actor="github-webhook",
            action="installation.deleted",
            target=str(installation_id),
            payload={"installation_id": installation_id, "rows_removed": removed},
            tenant_id=tenant_id,
        )
