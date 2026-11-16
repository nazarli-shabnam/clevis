"""Tamper-proof binding between a job and the installation its worker may fetch a fresh token for.

The worker's database role can UPDATE every column of ``jobs`` (migration 0020), so a compromised worker could
rewrite a running job's ``tenant_id`` or payload to point at another tenant's account. The refresh route
(``routers/internal_jobs.py``) therefore mints only when the payload carries a signature the API made at
enqueue time over (job id, tenant id, account login). The key is derived from ``AUTH_SECRET``, which the worker
never receives, so changing any signed value, or moving a signature to another job, invalidates it."""

import hashlib
import hmac
import time

from src.core.config import settings

# The payload field naming the GitHub account each job type works on.
ACCOUNT_FIELD = {
    "github.clear_actions_cache": "owner",
    "github.backfill_repo_events": "account_login",
    "github.backfill_security_alerts": "account_login",
    "github.reconcile_org_membership": "org_login",
}
SIG_FIELD = "refresh_sig"
EXP_FIELD = "refresh_exp"
# A signature stops working this long after enqueue, so a worker that kept one (or re-marks an old job as running)
# cannot keep minting tokens for that tenant indefinitely. Far longer than a job's queue wait plus its retries.
WINDOW_SECONDS = 12 * 3600


def _key() -> bytes:
    return hashlib.sha256(b"clevis-job-token-refresh|" + settings.auth_secret.get_secret_value().encode()).digest()


def sign(job_id: int, tenant_id: int, account_login: str, expires_at: int) -> str:
    message = f"{job_id}|{tenant_id}|{account_login.lower()}|{expires_at}".encode()
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()


def new_expiry() -> int:
    return int(time.time()) + WINDOW_SECONDS


def verify(signature: object, expires_at: object, job_id: int, tenant_id: int, account_login: str) -> bool:
    if not isinstance(signature, str) or isinstance(expires_at, bool) or not isinstance(expires_at, int):
        return False
    if expires_at < time.time():
        return False
    return hmac.compare_digest(signature, sign(job_id, tenant_id, account_login, expires_at))
