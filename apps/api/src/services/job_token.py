"""The token fields of a background-job payload, shared by every code path that enqueues a job."""

from src.core._crypto import encrypt_job_token
from src.core.config import settings
from src.services import github_app


def job_token_fields(token: str) -> dict:
    """``{"token": <Fernet-encrypted token>}`` plus, for an installation token this process minted,
    ``"token_expires_at"`` (epoch seconds).

    The worker has no GitHub App key, so it cannot mint a fresh token for a queued or retried job. It checks
    ``token_expires_at`` before every attempt instead and fails the job with a clear message once the token
    has lapsed (queue wait + backoff + earlier attempts can outlast the ~1h token). A PAT has no known expiry,
    so it carries no field and is never refused."""
    fields: dict = {"token": encrypt_job_token(token, settings.job_secret_key.get_secret_value())}
    expires_at = github_app.token_expires_at(token)
    if expires_at is not None:
        fields["token_expires_at"] = expires_at
    return fields
