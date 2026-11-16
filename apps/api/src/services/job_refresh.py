"""Tamper-proof binding between a job and the installation its worker may fetch a fresh token for.

The worker's database role can UPDATE every column of ``jobs`` (migration 0020), so a compromised worker could
rewrite a running job's ``tenant_id`` or payload to point at another tenant's account. The refresh route
(``routers/internal_jobs.py``) therefore mints only when the payload carries a signature the API made at
enqueue time over (job id, tenant id, account login). The key is derived from ``AUTH_SECRET``, which the worker
never receives, so changing any signed value, or moving a signature to another job, invalidates it."""

import hashlib
import hmac

from src.core.config import settings

# The payload field naming the GitHub account each job type works on.
ACCOUNT_FIELD = {
    "github.clear_actions_cache": "owner",
    "github.backfill_repo_events": "account_login",
    "github.backfill_security_alerts": "account_login",
    "github.reconcile_org_membership": "org_login",
}
SIG_FIELD = "refresh_sig"


def _key() -> bytes:
    return hashlib.sha256(b"clevis-job-token-refresh|" + settings.auth_secret.get_secret_value().encode()).digest()


def sign(job_id: int, tenant_id: int, account_login: str) -> str:
    message = f"{job_id}|{tenant_id}|{account_login.lower()}".encode()
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()


def verify(signature: object, job_id: int, tenant_id: int, account_login: str) -> bool:
    return isinstance(signature, str) and hmac.compare_digest(signature, sign(job_id, tenant_id, account_login))
