"""Redis Streams consumer: normalizes queued webhook deliveries into repo_events and friends.

Uses a consumer group so restarts resume from Redis-held state; XPENDING + XCLAIM reclaim
entries from a crashed consumer, and poison pills past _MAX_DELIVERY_ATTEMPTS are XACKed
(webhook_deliveries keeps the raw payload).
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import psycopg
import redis

import org_membership_store
import repo_events_store
import security_alerts_store
from config import plain_db_url, settings

log = logging.getLogger(__name__)

_DB_URL = plain_db_url(settings.database_url.get_secret_value())

# Must match apps/api/src/routers/webhooks.py's _WEBHOOK_STREAM_KEY (no shared code).
_STREAM_KEY = "webhook_events"
_GROUP_NAME = "event_processors"
_CONSUMER_NAME = f"worker-{os.getpid()}"

# Security alert events normalize into security_alerts (upsert-on-state-change), not repo_events.
_SECURITY_ALERT_EVENT_TYPES = {"dependabot_alert", "code_scanning_alert", "secret_scanning_alert"}

# member/organization normalize into org_members/repo_collaborators.
# membership/team: safety net for already-queued events until a normalizer exists.
_ORG_MEMBERSHIP_EVENT_TYPES = {"member", "organization"}
_NOT_YET_NORMALIZED_EVENT_TYPES = {"membership", "team"}

# Idle time before a claimed-but-unacked entry is reclaimed.
_RECLAIM_IDLE_MS = 60_000
# A repeatedly-failing entry is dropped so a poison pill can't block reclaim forever.
_MAX_DELIVERY_ATTEMPTS = 5
# How long one member/organization event waits for its tenant's membership lock (held by a reconcile
# job across its GitHub calls) before it is set aside for the pending sweep. Short, because the consumer
# is a single thread: every other tenant's webhooks queue up behind a wait.
_TENANT_LOCK_WAIT_SECONDS = 5.0
# At most this many deferred entries are retried per loop iteration, so a big backlog behind one stuck
# tenant can't delay the pending sweep and new events; the rest wait for the next iteration.
_DEFERRED_RETRY_BUDGET = 50
# Bounded block so the loop still wakes to touch the heartbeat and sweep pending.
_BLOCK_MS = 5_000
_BATCH_SIZE = 10

# Separate from worker.py's HEARTBEAT_FILE; the healthcheck checks both so a hung
# consumer thread fails the container on its own.
_HEARTBEAT_FILE = Path("/tmp/worker_event_consumer_heartbeat")

_client: redis.Redis | None = None


def _redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=2,
            # Must exceed _BLOCK_MS, or redis-py raises a false TimeoutError on every idle poll.
            socket_timeout=(_BLOCK_MS / 1000) + 2,
        )
    return _client


def _touch_heartbeat() -> None:
    try:
        _HEARTBEAT_FILE.write_text(str(time.time()))
    except OSError as exc:
        log.warning("could not write event consumer heartbeat file %s: %s", _HEARTBEAT_FILE, exc)


def _ensure_group(client: redis.Redis) -> None:
    try:
        client.xgroup_create(_STREAM_KEY, _GROUP_NAME, id="0", mkstream=True)
    except redis.ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


def _summarize(event_type: str, payload: dict) -> str:
    """Per-event-type summary text from the raw webhook body."""
    if event_type == "push":
        commits = payload.get("commits") or []
        count = len(commits)
        branch = (payload.get("ref") or "").removeprefix("refs/heads/")
        noun = "commit" if count == 1 else "commits"
        return f"pushed {count} {noun} to {branch}" if branch else f"pushed {count} {noun}"

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action", "")
        verb = "merged" if action == "closed" and pr.get("merged") else action
        return f"{verb} PR #{payload.get('number')}: {pr.get('title', '')}"

    if event_type == "issues":
        issue = payload.get("issue") or {}
        action = payload.get("action", "")
        return f"{action} issue #{issue.get('number')}: {issue.get('title', '')}"

    if event_type == "release":
        release = payload.get("release") or {}
        return f"created release {release.get('tag_name', '')}"

    if event_type == "create":
        ref_type = payload.get("ref_type", "")
        ref = payload.get("ref") or ""
        return f"created {ref_type} {ref}".strip()

    return event_type


def _normalize(event_type: str, payload: dict, received_at: datetime) -> dict | None:
    """Return repo_events column values, or None for a malformed payload.

    The receiver only validates JSON-ness, so don't crash the loop on bad shapes."""
    repository = payload.get("repository") or {}
    sender = payload.get("sender") or {}
    repo_full_name = repository.get("full_name")
    if not repo_full_name:
        return None
    return {
        "event_type": event_type,
        "actor": sender.get("login", ""),
        "actor_avatar": sender.get("avatar_url", ""),
        "repo": repo_full_name,
        "summary": _summarize(event_type, payload),
        # No common top-level timestamp across payload shapes; use ingestion time.
        "occurred_at": received_at,
    }


# event_type -> security_alerts.kind, using GitHub REST API's alert vocabulary.
_ALERT_KIND_BY_EVENT_TYPE = {
    "dependabot_alert": "dependabot",
    "code_scanning_alert": "code_scanning",
    "secret_scanning_alert": "secret_scanning",
}


def _parse_alert_timestamp(value: str | None, fallback: datetime) -> datetime:
    """Parse GitHub's trailing-'Z' ISO 8601 (fromisoformat rejects it before 3.11).

    Falls back to received_at for a missing/malformed value."""
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def _normalize_security_alert(event_type: str, payload: dict, received_at: datetime) -> dict | None:
    """Return security_alerts column values, or None for a malformed payload."""
    repository = payload.get("repository") or {}
    repo_full_name = repository.get("full_name")
    alert = payload.get("alert") or {}
    number = alert.get("number")
    if not repo_full_name or number is None:
        return None

    kind = _ALERT_KIND_BY_EVENT_TYPE[event_type]
    if kind == "dependabot":
        severity = (alert.get("security_advisory") or {}).get("severity")
        details = {
            "action": payload.get("action"),
            "dependency": alert.get("dependency"),
            "security_advisory": alert.get("security_advisory"),
        }
    elif kind == "code_scanning":
        severity = (alert.get("rule") or {}).get("severity")
        details = {
            "action": payload.get("action"),
            "rule": alert.get("rule"),
            "tool": alert.get("tool"),
        }
    else:  # secret_scanning -- no severity in GitHub's payload for this alert type
        severity = None
        details = {
            "action": payload.get("action"),
            "secret_type": alert.get("secret_type"),
            "secret_type_display_name": alert.get("secret_type_display_name"),
            # Only present once the alert is resolved.
            "resolution": alert.get("resolution"),
        }

    return {
        "repo": repo_full_name,
        "kind": kind,
        "number": number,
        "state": alert.get("state", ""),
        "severity": severity,
        "details": details,
        "created_at": _parse_alert_timestamp(alert.get("created_at"), received_at),
        "updated_at": _parse_alert_timestamp(alert.get("updated_at"), received_at),
    }


def _normalize_member_event(payload: dict) -> dict | None:
    """Return repo_collaborators column values for a `member` event, or None if malformed.

    `changes.permission` is optional on both 'added' and 'edited' per GitHub's schemas, so
    "unknown" is a legitimate fallback. is_outside_collaborator is None: this event can't
    determine it; the reconciliation poll fills it in."""
    repository = payload.get("repository") or {}
    repo_full_name = repository.get("full_name")
    member = payload.get("member") or {}
    login = member.get("login")
    if not repo_full_name or not login:
        return None

    permission = ((payload.get("changes") or {}).get("permission") or {}).get("to")
    return {
        "repo": repo_full_name,
        "login": login,
        "permission": permission or "unknown",
        "is_outside_collaborator": None,
    }


def _normalize_organization_event(payload: dict) -> dict | None:
    """Return org_members column values for member_added/member_removed, else None.

    `role`/`avatar_url` use `or` fallbacks: GitHub can send explicit nulls, and both
    columns are NOT NULL, so a None would leave the entry unacked forever."""
    membership = payload.get("membership") or {}
    user = membership.get("user") or {}
    login = user.get("login")
    if not login:
        return None
    return {
        "login": login,
        "avatar_url": user.get("avatar_url") or "",
        "role": membership.get("role") or "member",
    }


def _mark_dropped(pg_conn: psycopg.Connection, delivery_row_id: str | int) -> None:
    """Give a delivery the consumer is abandoning a terminal status. Left at 'queued' it would look stuck,
    and webhook_requeue_sweep would re-queue it every tick forever (and crowd out genuinely stuck rows)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "UPDATE webhook_deliveries SET status = 'dropped' WHERE id = %s AND status IN ('queued', 'queue_failed')",
            (delivery_row_id,),
        )
    pg_conn.commit()


class TenantLockBusy(Exception):
    """The tenant's membership lock is held elsewhere, so the entry can't be processed yet."""


# Membership entries set aside because their tenant's lock was busy, per tenant in arrival order. They stay
# unacked in Redis, so a crash still recovers them through the pending sweep. Kept locally (rather than
# left to that sweep) so that waiting costs no delivery attempt (a removal must not be dropped for
# waiting out a long reconcile) and so a tenant's later events can't overtake an earlier one (a stale
# "added" processed after a newer "removed" would resurrect access). Only the consumer thread touches it.
_deferred: dict[str, deque] = {}
# Failed (not merely blocked) attempts per deferred entry id; at _MAX_DELIVERY_ATTEMPTS it is dropped like
# any poison pill, so a permanently failing head can't hold its tenant's later events back forever.
_deferred_failures: dict[str, int] = {}


def _tenant_key(fields: dict) -> str:
    return str(fields.get("tenant_id") or "")


def _dispatch(pg_conn: psycopg.Connection, redis_client: redis.Redis, entry_id: str, fields: dict) -> None:
    """_process_entry, but a membership entry whose tenant lock is busy (or that has an earlier entry
    waiting on it) is queued locally instead of raising."""
    key = _tenant_key(fields)
    if fields.get("event_type") in _ORG_MEMBERSHIP_EVENT_TYPES and key in _deferred:
        _deferred[key].append((entry_id, fields))
        return
    try:
        _process_entry(pg_conn, redis_client, entry_id, fields)
    except TenantLockBusy:
        log.info("stream entry %s deferred: membership lock for tenant %s is busy", entry_id, key)
        _deferred.setdefault(key, deque()).append((entry_id, fields))


def _retry_deferred(pg_conn: psycopg.Connection, redis_client: redis.Redis) -> None:
    """Non-waiting attempts, oldest entry of each tenant first, at most _DEFERRED_RETRY_BUDGET per call.

    A tenant stops at its first entry that is still blocked or just failed: later entries must not
    overtake it. A failing head is retried on later calls and dropped after _MAX_DELIVERY_ATTEMPTS."""
    budget = _DEFERRED_RETRY_BUDGET
    for key in list(_deferred):
        queue = _deferred[key]
        while queue and budget > 0:
            entry_id, fields = queue[0]
            budget -= 1
            try:
                _process_entry(pg_conn, redis_client, entry_id, fields, lock_wait_s=0)
            except TenantLockBusy:
                break
            except Exception:
                log.exception("failed to process deferred stream entry %s", entry_id)
                _rollback_quietly(pg_conn)
                failures = _deferred_failures.get(entry_id, 0) + 1
                if failures < _MAX_DELIVERY_ATTEMPTS:
                    _deferred_failures[entry_id] = failures
                    break
                log.error("deferred stream entry %s failed %d times, dropping", entry_id, failures)
                row_id = fields.get("delivery_row_id")
                if row_id is not None:
                    _mark_dropped(pg_conn, row_id)
                redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
            _deferred_failures.pop(entry_id, None)
            queue.popleft()
        if not queue:
            _deferred.pop(key, None)
        if budget <= 0:
            break


def _process_entry(
    pg_conn: psycopg.Connection,
    redis_client: redis.Redis,
    entry_id: str,
    fields: dict,
    lock_wait_s: float | None = None,
) -> None:
    delivery_row_id = fields.get("delivery_row_id")
    if delivery_row_id is None:
        log.error("stream entry %s missing delivery_row_id, dropping: %r", entry_id, fields)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, delivery_id, event_type, payload, received_at FROM webhook_deliveries WHERE id = %s",
            (delivery_row_id,),
        )
        row = cur.fetchone()

    if row is None:
        log.error("webhook_deliveries row %s referenced by stream entry %s not found, dropping", delivery_row_id, entry_id)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    tenant_id, delivery_id, event_type, payload_bytes, received_at = row
    if tenant_id is None:
        # No tenant to scope a normalized row to. Ack so it doesn't sit pending forever;
        # the raw payload stays in webhook_deliveries.
        log.warning("webhook_deliveries row %s has no tenant_id, skipping normalization", delivery_row_id)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    if event_type in _NOT_YET_NORMALIZED_EVENT_TYPES:
        # No normalizer for membership/team. Ack, but leave webhook_deliveries.status
        # 'queued' so a future consumer can still find it.
        log.debug("webhook_deliveries row %s is a %s event with no consumer yet, leaving queued", delivery_row_id, event_type)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        log.error("webhook_deliveries row %s has malformed JSON payload, dropping", delivery_row_id)
        _mark_dropped(pg_conn, delivery_row_id)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    if event_type in _ORG_MEMBERSHIP_EVENT_TYPES:
        # Held for the whole write so it can't land between the reconciliation job's roster
        # fetch and snapshot apply.
        if org_membership_store.try_acquire_tenant_lock(
            pg_conn,
            tenant_id,
            timeout_s=_TENANT_LOCK_WAIT_SECONDS if lock_wait_s is None else lock_wait_s,
            on_wait=_touch_heartbeat,
        ) is None:
            raise TenantLockBusy(tenant_id)
        try:
            with pg_conn.cursor() as cur:
                cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
                if event_type == "member":
                    member_normalized = _normalize_member_event(payload)
                    action = payload.get("action")
                    if member_normalized is None:
                        log.error("webhook_deliveries row %s has no repository.full_name or member.login, dropping", delivery_row_id)
                        _mark_dropped(pg_conn, delivery_row_id)
                        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
                        return
                    if action == "removed":
                        org_membership_store.remove_repo_collaborator(
                            cur, tenant_id=tenant_id, repo=member_normalized["repo"], login=member_normalized["login"],
                            event_received_at=received_at,
                        )
                    else:
                        org_membership_store.upsert_repo_collaborator(cur, tenant_id=tenant_id, granted_at=received_at, **member_normalized)
                else:  # organization
                    action = payload.get("action")
                    if action == "member_removed":
                        login = ((payload.get("membership") or {}).get("user") or {}).get("login")
                        if not login:
                            log.error("webhook_deliveries row %s has no membership.user.login, dropping", delivery_row_id)
                            _mark_dropped(pg_conn, delivery_row_id)
                            redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
                            return
                        org_membership_store.remove_org_member(cur, tenant_id=tenant_id, login=login, event_received_at=received_at)
                        gh_user_id = ((payload.get("membership") or {}).get("user") or {}).get("id")
                        # A surviving org_members row means this removal is older than a re-add.
                        cur.execute("SELECT 1 FROM org_members WHERE tenant_id = %s AND login = %s", (tenant_id, login))
                        if isinstance(gh_user_id, int) and cur.fetchone() is None:
                            org_membership_store.revoke_github_membership(
                                cur, tenant_id=tenant_id, github_user_id=gh_user_id, login=login
                            )
                    elif action == "member_added":
                        org_normalized = _normalize_organization_event(payload)
                        if org_normalized is None:
                            log.error("webhook_deliveries row %s has no membership.user.login, dropping", delivery_row_id)
                            _mark_dropped(pg_conn, delivery_row_id)
                            redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
                            return
                        org_membership_store.upsert_org_member(cur, tenant_id=tenant_id, added_at=received_at, **org_normalized)
                    else:
                        # member_invited/renamed/deleted don't affect org_members -- ack as a no-op.
                        log.debug("webhook_deliveries row %s is an organization/%s event, no-op for org_members", delivery_row_id, action)
                cur.execute("UPDATE webhook_deliveries SET status = 'processed' WHERE id = %s", (delivery_row_id,))
            pg_conn.commit()
            redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
            return
        except BaseException:
            # Any failure, not just psycopg.Error, must roll back before release_tenant_lock:
            # that call commits, so a KeyError/TypeError raised after a write would otherwise
            # commit the partial write. For a DB error it also avoids pg_advisory_unlock failing
            # on an aborted transaction and leaking the lock. Re-raised for the caller's handling.
            pg_conn.rollback()
            raise
        finally:
            org_membership_store.release_tenant_lock(pg_conn, tenant_id)

    if event_type in _SECURITY_ALERT_EVENT_TYPES:
        alert_normalized = _normalize_security_alert(event_type, payload, received_at)
        if alert_normalized is None:
            log.error("webhook_deliveries row %s has no repository.full_name or alert.number, dropping", delivery_row_id)
            _mark_dropped(pg_conn, delivery_row_id)
            redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
            return

        with pg_conn.cursor() as cur:
            # Session context for RLS; see the repo_events branch below.
            cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
            security_alerts_store.upsert_security_alert(cur, tenant_id=tenant_id, **alert_normalized)
            cur.execute("UPDATE webhook_deliveries SET status = 'processed' WHERE id = %s", (delivery_row_id,))
        pg_conn.commit()
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    normalized = _normalize(event_type, payload, received_at)
    if normalized is None:
        log.error("webhook_deliveries row %s has no repository.full_name, dropping", delivery_row_id)
        _mark_dropped(pg_conn, delivery_row_id)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)
        return

    with pg_conn.cursor() as cur:
        # Plain SET (not SET LOCAL) read by this table's RLS policy, scoped to this
        # connection -- narrower than granting clevis_worker BYPASSRLS.
        cur.execute(f"SET app.tenant_id = {int(tenant_id)}")
        repo_events_store.insert_event_and_upsert_daily_count(cur, tenant_id=tenant_id, delivery_id=delivery_id, **normalized)
        cur.execute("UPDATE webhook_deliveries SET status = 'processed' WHERE id = %s", (delivery_row_id,))
    pg_conn.commit()
    redis_client.xack(_STREAM_KEY, _GROUP_NAME, entry_id)


def _rollback_quietly(pg_conn: psycopg.Connection) -> None:
    """Clear an aborted transaction after a failed entry so the shared connection stays
    usable for the rest of the batch."""
    try:
        pg_conn.rollback()
    except Exception:  # noqa: BLE001 -- best effort; the outer loop reconnects on a dead conn
        log.exception("rollback after a failed stream entry itself failed")


def _sweep_pending(pg_conn: psycopg.Connection, redis_client: redis.Redis) -> None:
    """Reclaim entries idle past _RECLAIM_IDLE_MS, or drop ones that failed too many times."""
    # Entries waiting locally on a busy tenant lock are being handled; reclaiming them would re-run them
    # out of order and spend delivery attempts on a wait. They are filtered out, and the listing is paged
    # past them, so a first page made up entirely of waiting entries can't hide the ones behind it.
    waiting = {entry_id for queue in _deferred.values() for entry_id, _ in queue}
    pending: list = []
    cursor = "-"
    while True:
        page = redis_client.xpending_range(
            _STREAM_KEY, _GROUP_NAME, min=cursor, max="+", count=100, idle=_RECLAIM_IDLE_MS
        )
        pending = [p for p in page if p["message_id"] not in waiting]
        if pending or len(page) < 100:
            break
        cursor = f"({page[-1]['message_id']}"  # exclusive: continue after the last entry seen
    if not pending:
        return

    # >= / <, not > / <=: XCLAIM increments times_delivered before processing, so
    # otherwise an entry at the limit gets one extra attempt.
    to_drop = [p["message_id"] for p in pending if p["times_delivered"] >= _MAX_DELIVERY_ATTEMPTS]
    to_claim = [p["message_id"] for p in pending if p["times_delivered"] < _MAX_DELIVERY_ATTEMPTS]

    for message_id in to_drop:
        log.error("stream entry %s exceeded %d delivery attempts, dropping", message_id, _MAX_DELIVERY_ATTEMPTS)
        for _entry_id, entry_fields in redis_client.xrange(_STREAM_KEY, min=message_id, max=message_id):
            row_id = entry_fields.get("delivery_row_id")
            if row_id is not None:
                _mark_dropped(pg_conn, row_id)
        redis_client.xack(_STREAM_KEY, _GROUP_NAME, message_id)

    if not to_claim:
        return
    claimed = redis_client.xclaim(_STREAM_KEY, _GROUP_NAME, _CONSUMER_NAME, min_idle_time=_RECLAIM_IDLE_MS, message_ids=to_claim)
    for entry_id, fields in claimed:
        try:
            _dispatch(pg_conn, redis_client, entry_id, fields)
        except Exception:
            log.exception("failed to process reclaimed stream entry %s", entry_id)
            _rollback_quietly(pg_conn)


def run() -> None:
    # Retried: this daemon thread has no supervisor, so an uncaught exception (e.g. Redis
    # not up yet at startup) would kill the consumer silently.
    redis_client = None
    while redis_client is None:
        try:
            redis_client = _redis_client()
            _ensure_group(redis_client)
        except (redis.RedisError, OSError):
            log.exception("event consumer initialization error")
            redis_client = None
            time.sleep(5)
    log.info("event consumer started, group=%s consumer=%s", _GROUP_NAME, _CONSUMER_NAME)

    while True:
        _touch_heartbeat()
        try:
            with psycopg.connect(_DB_URL) as pg_conn:
                _retry_deferred(pg_conn, redis_client)
                _sweep_pending(pg_conn, redis_client)

                response = redis_client.xreadgroup(
                    _GROUP_NAME, _CONSUMER_NAME, {_STREAM_KEY: ">"}, count=_BATCH_SIZE, block=_BLOCK_MS
                )
                for _stream_key, entries in response or []:
                    for entry_id, fields in entries:
                        try:
                            _dispatch(pg_conn, redis_client, entry_id, fields)
                        except Exception:
                            # Left unacked on purpose; _sweep_pending reclaims it later. Roll back first so
                            # an aborted transaction doesn't poison the rest of the batch.
                            log.exception("failed to process stream entry %s", entry_id)
                            _rollback_quietly(pg_conn)
        except (psycopg.OperationalError, redis.RedisError):
            log.exception("event consumer connection error")
            time.sleep(5)
        except Exception:
            log.exception("event consumer loop error")
            time.sleep(5)


def start_background_thread() -> threading.Thread:
    thread = threading.Thread(target=run, daemon=True, name="event-consumer")
    thread.start()
    return thread
