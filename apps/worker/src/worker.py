import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
from pydantic import BaseModel, Field, ValidationError

import backfill
import membership_reconcile
import org_membership_store
import repo_events_store
from _crypto import decrypt_job_token
from _sanitize import sanitize_error
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Touched each loop so the healthcheck can detect a hung (not just crashed) worker.
HEARTBEAT_FILE = Path("/tmp/worker_heartbeat")
# Healthcheck treats the heartbeat as stale past 60s; cap the live-editable poll
# interval well below that so a normal sleep can't look like a hang.
_MAX_POLL_SECONDS = 30

# psycopg.connect() expects plain postgresql://, not the SQLAlchemy +psycopg dialect prefix
_CONNECT_TIMEOUT_SECONDS = 5


def _plain_db_url(url: str) -> str:
    """Strip the SQLAlchemy dialect prefix and bound connect time.

    Without connect_timeout, psycopg.connect() can hang indefinitely on the hot poll path."""
    stripped = url.replace("postgresql+psycopg://", "postgresql://")
    sep = "&" if "?" in stripped else "?"
    return f"{stripped}{sep}connect_timeout={_CONNECT_TIMEOUT_SECONDS}"


_DB_URL = _plain_db_url(settings.database_url.get_secret_value())

# Shared cap on retry_count for both reclaim-after-crash and transient-failure requeue.
MAX_RETRIES = 5
# Worst case ~23 min of total backoff across MAX_RETRIES -- well inside the 1h lifetime of
# the installation token a job payload carries.
_RETRY_BACKOFF_BASE_SECONDS = 30
_RETRY_BACKOFF_CAP_SECONDS = 480
# A 'processing' job older than this, with a stale heartbeat, is presumed crashed.
RECLAIM_TIMEOUT_MINUTES = 30

# Kept well below RECLAIM_TIMEOUT_MINUTES so a slow-but-alive job stays fresh.
_JOB_HEARTBEAT_INTERVAL_SECONDS = 10
# Consecutive failed heartbeat writes after which the log escalates from warning to error: the job
# row is then drifting toward "looks crashed" (the job lock below still keeps it from being reclaimed).
_HEARTBEAT_FAILURES_BEFORE_ERROR = 3

# Per-job session-level advisory lock, held by the worker that claimed the job for as long as it
# processes it. The reclaim sweep skips a job whose lock is held, so a heartbeat that can't be written
# can't get a still-running job reclaimed and run twice (duplicate cache deletes, duplicate backfill
# fetches). It is released on completion, and Postgres drops it if the worker's connection dies, so a
# genuinely crashed worker's job is still reclaimed. Two-int form: (namespace hash, job id).
_JOB_LOCK_NAMESPACE = "clevis.jobs.processing"


def _read_app_config(key: str, default: str) -> str:
    """Read a single value from app_config. Falls back to default on any error."""
    try:
        with psycopg.connect(_DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
                row = cur.fetchone()
        return row[0] if row else default
    except Exception as exc:
        log.warning("Could not read app_config[%r]: %s; using default %r", key, exc, default)
        return default


def _read_poll_seconds() -> int:
    """Read worker_poll_seconds, clamped to [1, _MAX_POLL_SECONDS]; 5 on a malformed value."""
    raw = _read_app_config("worker_poll_seconds", "5")
    try:
        return max(1, min(_MAX_POLL_SECONDS, int(raw)))
    except ValueError:
        log.warning("worker_poll_seconds %r is not an integer; using 5", raw)
        return 5


class ClearActionsCachePayload(BaseModel):
    owner: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    token: str = Field(min_length=1)
    key: str | None = None
    ref: str | None = None


class BackfillRepoEventsPayload(BaseModel):
    tenant_id: int
    account_login: str = Field(min_length=1)
    account_type: str = Field(min_length=1)
    token: str = Field(min_length=1)


class ReconcileOrgMembershipPayload(BaseModel):
    tenant_id: int
    org_login: str = Field(min_length=1)
    token: str = Field(min_length=1)


def _fenced_update(conn: psycopg.Connection, job_id: int, sql: str, params: tuple) -> bool:
    """Run a status UPDATE fenced on (status='processing', retry_count) and commit.

    Returns False when the fence matched no row: another worker already reclaimed or
    finished the job (issue #253), so this worker's write never landed."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        applied = cur.rowcount == 1
    conn.commit()
    if not applied:
        log.warning("job %d already claimed by another worker, skipping status update", job_id)
    return applied


def _mark_done(conn: psycopg.Connection, job_id: int, result: dict, expected_retry_count: int) -> bool:
    # The retry_count match fences against reclaim resetting this job and a second
    # worker re-claiming it (status is 'processing' again, but retry_count was bumped).
    return _fenced_update(
        conn,
        job_id,
        "UPDATE jobs SET status='done', result=%s, payload=jobs_scrub_token(payload), updated_at=NOW() "
        "WHERE id=%s AND status='processing' AND retry_count=%s",
        (json.dumps(result), job_id, expected_retry_count),
    )


def _mark_failed(conn: psycopg.Connection, job_id: int, error_text: str, expected_retry_count: int) -> bool:
    return _fenced_update(
        conn,
        job_id,
        "UPDATE jobs SET status='failed', result=%s, payload=jobs_scrub_token(payload), updated_at=NOW() "
        "WHERE id=%s AND status='processing' AND retry_count=%s",
        (error_text, job_id, expected_retry_count),
    )


def _requeue_for_retry(conn: psycopg.Connection, job_id: int, retry_count: int, error_text: str) -> bool:
    # Fence on the retry_count observed at claim time, same as _mark_done/_mark_failed.
    new_count = retry_count + 1
    if new_count > MAX_RETRIES:
        return _fenced_update(
            conn,
            job_id,
            "UPDATE jobs SET status='failed', retry_count=%s, result=%s, payload=jobs_scrub_token(payload), updated_at=NOW() "
            "WHERE id=%s AND status='processing' AND retry_count=%s",
            (new_count, f"exceeded max retry attempts ({MAX_RETRIES}): {error_text}", job_id, retry_count),
        )
    return _fenced_update(
        conn,
        job_id,
        "UPDATE jobs SET status='queued', retry_count=%s, result=%s, updated_at=NOW() "
        "WHERE id=%s AND status='processing' AND retry_count=%s",
        (new_count, error_text, job_id, retry_count),
    )


def process_job(conn: psycopg.Connection, job_id: int, job_type: str, payload_raw: str, retry_count: int = 0) -> None:
    handler = JOB_HANDLERS.get(job_type)
    if handler is None:
        log.error("job %d has no handler registered for job_type %r", job_id, job_type)
        _mark_failed(conn, job_id, f"no handler registered for job_type {job_type!r}", retry_count)
        return
    try:
        handler(conn, job_id, payload_raw, retry_count)
    except Exception as error:
        # Safety net for anything unhandled, so the job fails/retries now instead of
        # waiting for the reclaim sweep.
        log.error("job %d hit an unexpected error: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)


def _github_error_message(resp: httpx.Response) -> str:
    """Prefer GitHub's own error message over a bare status code in jobs.result."""
    try:
        message = resp.json().get("message")
    except (ValueError, AttributeError):
        message = None
    if message:
        return f"GitHub API error: {resp.status_code} - {message}"
    return f"GitHub API error: {resp.status_code}"


def _github_response_is_error(conn: psycopg.Connection, job_id: int, retry_count: int, resp: httpx.Response) -> bool:
    """Apply the transient/permanent split to one GitHub response.

    Returns True (after requeueing or failing the job) on an error, False otherwise."""
    rate_limited = resp.status_code == 429 or membership_reconcile._is_secondary_rate_limit(resp)
    if resp.status_code >= 500 or rate_limited:
        # 5xx, 429, and secondary-rate-limit 403 are transient: back off and retry.
        log.warning("job %d got a %d from GitHub (attempt %d)", job_id, resp.status_code, retry_count + 1)
        _requeue_for_retry(conn, job_id, retry_count, sanitize_error(_github_error_message(resp)))
        return True
    if resp.status_code >= 300:
        log.error("job %d failed: GitHub API error %d", job_id, resp.status_code)
        _mark_failed(conn, job_id, sanitize_error(_github_error_message(resp)), retry_count)
        return True
    return False


def _handle_clear_actions_cache(conn: psycopg.Connection, job_id: int, payload_raw: str, retry_count: int) -> None:
    try:
        payload = ClearActionsCachePayload.model_validate_json(payload_raw)
    except ValidationError as error:
        log.error("job %d has an invalid payload: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)
        return

    try:
        token = decrypt_job_token(payload.token, settings.job_secret_key.get_secret_value())
    except Exception as error:
        log.error("job %d failed to decrypt its token: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)
        return

    base = settings.github_api_base
    repo_path = f"{base}/repos/{payload.owner}/{payload.repo}/actions/caches"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        with httpx.Client(timeout=20) as client:
            if payload.key:
                # Targeted clear: GitHub's DELETE-by-key does the work in one call.
                params = {k: v for k, v in (("key", payload.key), ("ref", payload.ref)) if v}
                resp = client.delete(repo_path, headers=headers, params=params)
                if _github_response_is_error(conn, job_id, retry_count, resp):
                    return
                if _mark_done(conn, job_id, {"ok": True, "status": resp.status_code}, retry_count):
                    log.info("job %d done", job_id)
                return

            # GitHub has no bulk-delete endpoint (keyless DELETE is a 422): list every
            # cache entry, then delete each by id.
            cache_ids: list[int] = []
            page = 1
            while True:
                resp = client.get(repo_path, headers=headers, params={"per_page": 100, "page": page})
                if _github_response_is_error(conn, job_id, retry_count, resp):
                    return
                batch = (resp.json() or {}).get("actions_caches", [])
                cache_ids.extend(entry["id"] for entry in batch if entry.get("id") is not None)
                if len(batch) < 100:
                    break
                page += 1

            deleted = 0
            for cache_id in cache_ids:
                resp = client.delete(f"{repo_path}/{cache_id}", headers=headers)
                if resp.status_code == 404:
                    # Already gone (retry after partial success, or a concurrent clear).
                    continue
                if _github_response_is_error(conn, job_id, retry_count, resp):
                    return
                deleted += 1
    except httpx.RequestError as error:
        log.warning("job %d hit a network error (attempt %d): %s", job_id, retry_count + 1, error)
        _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
        return

    if _mark_done(conn, job_id, {"ok": True, "deleted": deleted}, retry_count):
        log.info("job %d done (%d cache entries deleted)", job_id, deleted)


def _handle_backfill_repo_events(conn: psycopg.Connection, job_id: int, payload_raw: str, retry_count: int) -> None:
    try:
        payload = BackfillRepoEventsPayload.model_validate_json(payload_raw)
    except ValidationError as error:
        log.error("job %d has an invalid payload: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)
        return

    try:
        token = decrypt_job_token(payload.token, settings.job_secret_key.get_secret_value())
    except Exception as error:
        log.error("job %d failed to decrypt its token: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)
        return

    base = settings.github_api_base
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        with httpx.Client(timeout=20) as client:
            raw_events = backfill.fetch_events(client, base, headers, payload.account_login, payload.account_type)
    except httpx.HTTPStatusError as error:
        resp = error.response
        if resp.status_code >= 500:
            # 5xx is presumed transient (GitHub-side issue) — worth retrying, unlike 4xx.
            log.warning("job %d got a %d from GitHub (attempt %d)", job_id, resp.status_code, retry_count + 1)
            _requeue_for_retry(conn, job_id, retry_count, sanitize_error(_github_error_message(resp)))
        else:
            log.error("job %d failed: GitHub API error %d", job_id, resp.status_code)
            _mark_failed(conn, job_id, sanitize_error(_github_error_message(resp)), retry_count)
        return
    except httpx.RequestError as error:
        log.warning("job %d hit a network error (attempt %d): %s", job_id, retry_count + 1, error)
        _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
        return

    inserted_count = 0
    skipped_duplicates = 0
    try:
        with conn.cursor() as cur:
            # Session context for RLS, same as event_consumer.py's _process_entry.
            cur.execute(f"SET app.tenant_id = {int(payload.tenant_id)}")
            normalized_events = [n for n in map(backfill.normalize, raw_events) if n is not None]
            # An event the webhook path already stored must not be stored again under a backfill id,
            # or repo_events and the daily rollup count it twice (issue #542).
            webhook_twins = (
                repo_events_store.webhook_events_between(
                    cur,
                    tenant_id=payload.tenant_id,
                    start=min(n["occurred_at"] for n in normalized_events),
                    end=max(n["occurred_at"] for n in normalized_events),
                )
                if normalized_events
                else {}
            )
            for normalized in normalized_events:
                if repo_events_store.consume_webhook_twin(
                    webhook_twins,
                    repo=normalized["repo"],
                    event_type=normalized["event_type"],
                    actor=normalized["actor"],
                    occurred_at=normalized["occurred_at"],
                ):
                    skipped_duplicates += 1
                    continue
                if repo_events_store.insert_event_and_upsert_daily_count(cur, tenant_id=payload.tenant_id, **normalized):
                    inserted_count += 1

            # Upserted in the same transaction so a rollback doesn't advance the cursor.
            cur.execute(
                """
                INSERT INTO activity_sync_cursors (tenant_id, account_login, account_type, last_synced_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (tenant_id) DO UPDATE SET
                    account_login = EXCLUDED.account_login,
                    account_type = EXCLUDED.account_type,
                    last_synced_at = EXCLUDED.last_synced_at,
                    updated_at = NOW()
                """,
                (payload.tenant_id, payload.account_login, payload.account_type),
            )
        conn.commit()
    except psycopg.Error as error:
        # Roll back so the aborted transaction doesn't break the safety-net UPDATE;
        # synthetic delivery_ids make a full retry safe.
        conn.rollback()
        log.warning("job %d failed to store backfilled events (attempt %d): %s", job_id, retry_count + 1, error)
        _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
        return

    if _mark_done(
        conn,
        job_id,
        {
            "ok": True,
            "events_seen": len(raw_events),
            "events_inserted": inserted_count,
            "events_skipped_duplicate": skipped_duplicates,
        },
        retry_count,
    ):
        log.info(
            "job %d done: backfilled %d/%d events for %s", job_id, inserted_count, len(raw_events), payload.account_login
        )


def _handle_reconcile_org_membership(conn: psycopg.Connection, job_id: int, payload_raw: str, retry_count: int) -> None:
    try:
        payload = ReconcileOrgMembershipPayload.model_validate_json(payload_raw)
    except ValidationError as error:
        log.error("job %d has an invalid payload: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)
        return

    try:
        token = decrypt_job_token(payload.token, settings.job_secret_key.get_secret_value())
    except Exception as error:
        log.error("job %d failed to decrypt its token: %s", job_id, error)
        _mark_failed(conn, job_id, sanitize_error(error), retry_count)
        return

    base = settings.github_api_base
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Held from before the roster fetch until the snapshot commits; must span the HTTP
    # round trip, so it can't be transaction-scoped.
    org_membership_store.acquire_tenant_lock(conn, payload.tenant_id)
    try:
        try:
            with httpx.Client(timeout=20) as client:
                roster = membership_reconcile.fetch_org_roster(client, base, headers, payload.org_login)
        except httpx.HTTPStatusError as error:
            resp = error.response
            # Rate limit still in effect after _get_with_retry's backoff: requeue for a later
            # attempt rather than failing like a genuine 4xx.
            if resp.status_code == 429 or membership_reconcile._is_secondary_rate_limit(resp) or resp.status_code >= 500:
                log.warning("job %d got a %d from GitHub (attempt %d)", job_id, resp.status_code, retry_count + 1)
                _requeue_for_retry(conn, job_id, retry_count, sanitize_error(_github_error_message(resp)))
            else:
                log.error("job %d failed: GitHub API error %d", job_id, resp.status_code)
                _mark_failed(conn, job_id, sanitize_error(_github_error_message(resp)), retry_count)
            return
        except httpx.RequestError as error:
            log.warning("job %d hit a network error (attempt %d): %s", job_id, retry_count + 1, error)
            _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
            return
        except membership_reconcile.RosterIncomplete as error:
            # An untrustworthy roster would DELETE real members; requeue, likely transient.
            log.warning("job %d got an incomplete roster (attempt %d): %s", job_id, retry_count + 1, error)
            _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
            return

        two_factor_disabled = roster["two_factor_disabled_logins"]
        members = [
            {
                **m,
                # None (overlay unavailable) stays None; else enabled iff not in the disabled set.
                "two_factor_enabled": None if two_factor_disabled is None else m["login"] not in two_factor_disabled,
            }
            for m in roster["members"]
        ]
        member_logins = {m["login"] for m in members}

        try:
            with conn.cursor() as cur:
                # Session context for RLS, same as event_consumer.py's _process_entry.
                cur.execute(f"SET app.tenant_id = {int(payload.tenant_id)}")
                synced_at = datetime.now(timezone.utc)
                org_membership_store.reconcile_org_members(cur, tenant_id=payload.tenant_id, members=members, synced_at=synced_at)
                org_membership_store.reconcile_repo_collaborator_outside_status(
                    cur, tenant_id=payload.tenant_id, member_logins=member_logins, outside_logins=roster["outside_logins"]
                )
                # Upserted in the same transaction so a rollback doesn't advance the cursor.
                cur.execute(
                    """
                    INSERT INTO org_membership_sync_cursors (tenant_id, org_login, last_synced_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        org_login = EXCLUDED.org_login,
                        last_synced_at = EXCLUDED.last_synced_at,
                        updated_at = NOW()
                    """,
                    (payload.tenant_id, payload.org_login),
                )
            conn.commit()
        except psycopg.Error as error:
            # Roll back so the aborted transaction doesn't leave the job stuck.
            conn.rollback()
            log.warning("job %d failed to store reconciled membership (attempt %d): %s", job_id, retry_count + 1, error)
            _requeue_for_retry(conn, job_id, retry_count, sanitize_error(error))
            return

        if _mark_done(conn, job_id, {"ok": True, "members_seen": len(members)}, retry_count):
            log.info("job %d done: reconciled %d members for %s", job_id, len(members), payload.org_login)
    finally:
        org_membership_store.release_tenant_lock(conn, payload.tenant_id)


# job_type -> handler(conn, job_id, payload_raw, retry_count); each handler owns its
# payload validation and terminal/retry outcome.
JOB_HANDLERS = {
    "github.clear_actions_cache": _handle_clear_actions_cache,
    "github.backfill_repo_events": _handle_backfill_repo_events,
    "github.reconcile_org_membership": _handle_reconcile_org_membership,
}


def _acquire_job_lock(conn: psycopg.Connection, job_id: int) -> bool:
    """Take the non-blocking per-job lock; False means another live worker already holds it."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), %s)", (_JOB_LOCK_NAMESPACE, job_id))
        acquired = bool(cur.fetchone()[0])
    conn.commit()
    return acquired


def _release_job_lock(conn: psycopg.Connection, job_id: int) -> None:
    """Release _acquire_job_lock's lock. Rolls back first: a handler that errored mid-transaction leaves
    the connection unusable, and a session-level lock survives a rollback. Never raises: the lock also
    dies with the connection, so a failed release must not mask the job's own outcome."""
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s), %s)", (_JOB_LOCK_NAMESPACE, job_id))
        conn.commit()
    except Exception as exc:
        log.warning("could not release the lock for job %d (it is dropped with the connection): %s", job_id, exc)


def _reclaim_stale_jobs(conn: psycopg.Connection) -> None:
    """Reset jobs stuck in 'processing' past RECLAIM_TIMEOUT_MINUTES back to 'queued'.

    Only reclaims when heartbeat_at is also stale (or null), since updated_at is set once at
    claim time, and never a job whose per-job lock is held: a live worker still owns it, whatever
    its heartbeat says. Shares retry_count/MAX_RETRIES with process_job so a crash loop ends 'failed'."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = CASE WHEN retry_count + 1 > %(max_retries)s THEN 'failed' ELSE 'queued' END,
                retry_count = retry_count + 1,
                result = CASE WHEN retry_count + 1 > %(max_retries)s THEN %(exceeded_message)s ELSE result END,
                payload = CASE WHEN retry_count + 1 > %(max_retries)s THEN jobs_scrub_token(payload) ELSE payload END,
                updated_at = NOW()
            WHERE status = 'processing'
              AND updated_at < NOW() - make_interval(mins => %(timeout_minutes)s)
              AND (heartbeat_at IS NULL OR heartbeat_at < NOW() - make_interval(mins => %(timeout_minutes)s))
              -- Skip a job a live worker holds the lock for (pg_locks shows the two-int advisory key
              -- as classid = namespace hash, unsigned; objid = job id).
              AND NOT EXISTS (
                  SELECT 1 FROM pg_locks l
                  WHERE l.locktype = 'advisory' AND l.granted AND l.objsubid = 2
                    AND l.classid = (hashtext(%(lock_namespace)s)::bigint & 4294967295)::oid
                    AND l.objid = jobs.id::oid
              )
            RETURNING id, status
            """,
            {
                "max_retries": MAX_RETRIES,
                "exceeded_message": f"exceeded max reclaim attempts ({MAX_RETRIES})",
                "timeout_minutes": RECLAIM_TIMEOUT_MINUTES,
                "lock_namespace": _JOB_LOCK_NAMESPACE,
            },
        )
        reclaimed = cur.fetchall()
    conn.commit()
    for job_id, status in reclaimed:
        log.warning("reclaimed stale job %d -> %s", job_id, status)


def _touch_job_heartbeat(job_id: int) -> bool:
    """Runs on its own connection; psycopg connections aren't thread-safe. Returns whether the
    jobs.heartbeat_at write succeeded."""
    ok = True
    try:
        with psycopg.connect(_DB_URL) as hb_conn:
            with hb_conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET heartbeat_at = NOW() WHERE id = %s AND status = 'processing'",
                    (job_id,),
                )
            hb_conn.commit()
    except Exception as exc:
        # Non-fatal: the per-job lock (not the heartbeat) is what keeps the reclaim sweep off a live job.
        ok = False
        log.warning("could not touch heartbeat for job %d: %s", job_id, exc)
    # Also refresh the file heartbeat, or a handler running past 60s would mark the
    # container unhealthy mid-job.
    _touch_heartbeat()
    return ok


class _JobHeartbeat:
    """Context manager: touches jobs.heartbeat_at every _JOB_HEARTBEAT_INTERVAL_SECONDS on a
    background thread while the block runs."""

    def __init__(self, job_id: int):
        self._job_id = job_id
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._consecutive_failures = 0

    def _tick(self) -> None:
        if _touch_job_heartbeat(self._job_id):
            self._consecutive_failures = 0
            return
        self._consecutive_failures += 1
        if self._consecutive_failures % _HEARTBEAT_FAILURES_BEFORE_ERROR == 0:
            # At error level (so alerting sees it, not just warnings), repeated every N failures for as long
            # as the streak lasts, so a long outage keeps producing an alertable line.
            log.error(
                "job %d heartbeat failed %d times in a row; its row will look crashed to the reclaim sweep "
                "(the job lock still protects it from a second worker)",
                self._job_id,
                self._consecutive_failures,
            )

    def _run(self) -> None:
        while not self._stop.wait(_JOB_HEARTBEAT_INTERVAL_SECONDS):
            self._tick()

    def __enter__(self) -> "_JobHeartbeat":
        self._tick()  # immediate first tick, don't wait a full interval
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop.set()
        self._thread.join(timeout=_JOB_HEARTBEAT_INTERVAL_SECONDS)


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_FILE.write_text(str(time.time()))
    except OSError as exc:
        # Non-fatal: the heartbeat is only a healthcheck liveness signal.
        log.warning("could not write heartbeat file %s: %s", HEARTBEAT_FILE, exc)


def run() -> None:
    poll_seconds = _read_poll_seconds()
    log.info("worker started, polling every %ds", poll_seconds)
    while True:
        _touch_heartbeat()
        # Re-read each cycle so settings changes take effect without restart.
        poll_seconds = _read_poll_seconds()
        try:
            with psycopg.connect(_DB_URL) as conn:
                _reclaim_stale_jobs(conn)

                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE jobs SET status = 'processing', updated_at = NOW()
                        WHERE id = (
                            SELECT id FROM jobs
                            WHERE status = 'queued'
                              -- Exponential backoff for retries (60s, 120s, ... capped at
                              -- _RETRY_BACKOFF_CAP_SECONDS) so a rate-limited job doesn't burn
                              -- all MAX_RETRIES in seconds. updated_at is stamped at requeue.
                              AND (retry_count = 0 OR updated_at <= NOW() - make_interval(
                                  secs => LEAST(%(base)s * power(2, retry_count), %(cap)s)))
                            ORDER BY id
                            LIMIT 1
                            FOR UPDATE SKIP LOCKED
                        )
                        RETURNING id, job_type, payload, retry_count
                    """, {"base": _RETRY_BACKOFF_BASE_SECONDS, "cap": _RETRY_BACKOFF_CAP_SECONDS})
                    row = cur.fetchone()

                if row:
                    conn.commit()
                    job_id = row[0]
                    if not _acquire_job_lock(conn, job_id):
                        # Only reachable if a live worker holds this job (reclaim skips locked jobs, so a
                        # second claim should be impossible). Never run it twice: hand it back to the queue
                        # (fenced on the retry_count we claimed, so it can't clobber the holder's own writes)
                        # to be retried after the usual backoff instead of sitting in 'processing' until reclaim.
                        log.error("job %d is locked by another live worker; requeueing instead of processing it", job_id)
                        _requeue_for_retry(conn, job_id, row[3], "another live worker holds this job")
                    else:
                        try:
                            with _JobHeartbeat(job_id):
                                process_job(conn, *row)
                        finally:
                            _release_job_lock(conn, job_id)
        except psycopg.OperationalError:
            log.error("database connection failed, retrying in %ds", poll_seconds)
        except Exception:
            log.exception("worker poll error")

        time.sleep(poll_seconds)


if __name__ == "__main__":
    import event_consumer

    # Event consumer on its own daemon thread; does not fail the container healthcheck on its own.
    event_consumer.start_background_thread()
    run()
