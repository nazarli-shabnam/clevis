"""The token fields baked into a background-job payload, and the sweep rule for a token-expired failure."""
import time
from datetime import datetime, timedelta, timezone

from src.core._crypto import decrypt_job_token
from src.core.config import settings
from src.repositories import job_repo, org_repo
from src.services import github_app
from src.services.job_token import job_token_fields
from src.services.sweep_lock import TOKEN_EXPIRED_PREFIX, has_blocking_job

_JOB_TYPE = "github.backfill_repo_events"


def test_installation_token_carries_its_expiry_and_a_pat_does_not(monkeypatch):
    expires = time.time() + 3000
    monkeypatch.setitem(github_app._cache, 101, github_app._CachedToken(token="ghs_installation", expires_at=expires))

    fields = job_token_fields("ghs_installation")
    assert fields["token_expires_at"] == expires
    assert decrypt_job_token(fields["token"], settings.job_secret_key.get_secret_value()) == "ghs_installation"

    # A token this process did not mint (a pasted PAT) has no known expiry, so the worker never refuses it.
    pat_fields = job_token_fields("ghp_pasted_pat")
    assert "token_expires_at" not in pat_fields
    assert decrypt_job_token(pat_fields["token"], settings.job_secret_key.get_secret_value()) == "ghp_pasted_pat"


def _failed_job(db, tenant_id, result):
    job_id = job_repo.enqueue(db, _JOB_TYPE, {"tenant_id": tenant_id}, tenant_id=tenant_id)
    job_repo.mark_failed(db, job_id, result)


def test_a_token_expired_failure_does_not_block_the_sweep_but_other_failures_do(db):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    expired_tenant = org_repo.get_or_create(db, github_login="expired-co").tenant_id
    broken_tenant = org_repo.get_or_create(db, github_login="broken-co").tenant_id

    _failed_job(db, expired_tenant, f"{TOKEN_EXPIRED_PREFIX} before the job could run; start it again")
    _failed_job(db, broken_tenant, "GitHub returned 404")

    assert has_blocking_job(db, _JOB_TYPE, expired_tenant, cutoff) is False
    assert has_blocking_job(db, _JOB_TYPE, broken_tenant, cutoff) is True
