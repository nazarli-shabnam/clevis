"""One place that answers "what are the GitHub App's credentials?".

Two sources, field by field: the ``GITHUB_APP_*`` environment variables win, otherwise the row Clevis stored when the
App was created through GitHub's manifest flow (``github_app_credentials``, secrets Fernet-encrypted with
``JOB_SECRET_KEY``). Existing installs that set the variables behave exactly as before.

The readers are called from code that has no database session (token minting, the webhook verifier, OAuth), so the
row is read on its own short-lived session and cached for a few seconds; ``save`` and ``clear_cache`` drop the cache so
a freshly created App is used without a restart. A database problem never raises here: it reads as "no stored
credentials" and is logged, because the rest of Clevis works without the App."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token, encrypt_job_token
from src.core.config import settings
from src.core.db import GitHubAppCredentials, SessionLocal

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 15


@dataclass(frozen=True)
class StoredCredentials:
    app_id: str
    slug: str | None
    client_id: str | None
    client_secret: str | None
    webhook_secret: str | None
    private_key: str


_lock = threading.Lock()
_cache: tuple[StoredCredentials | None, float] | None = None  # (row, monotonic expiry)


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _decrypt(value: str | None) -> str | None:
    return decrypt_job_token(value, settings.job_secret_key.get_secret_value()) if value else None


def _load() -> StoredCredentials | None:
    try:
        with SessionLocal() as db:
            row = db.get(GitHubAppCredentials, 1)
            if row is None:
                return None
            return StoredCredentials(
                app_id=row.app_id,
                slug=row.slug,
                client_id=row.client_id,
                client_secret=_decrypt(row.client_secret_enc),
                webhook_secret=_decrypt(row.webhook_secret_enc),
                private_key=_decrypt(row.private_key_enc) or "",
            )
    except Exception:
        logger.exception("could not read the stored GitHub App credentials; treating them as absent")
        return None


def stored() -> StoredCredentials | None:
    global _cache
    with _lock:
        if _cache is not None and time.monotonic() < _cache[1]:
            return _cache[0]
    value = _load()  # outside the lock: a DB round trip must not serialise every caller
    with _lock:
        _cache = (value, time.monotonic() + _CACHE_TTL_SECONDS)
    return value


def _secret(env_value) -> str | None:
    return env_value.get_secret_value() if env_value is not None and env_value.get_secret_value() else None


def app_id() -> str | None:
    return settings.github_app_id or (s.app_id if (s := stored()) else None)


def private_key() -> str | None:
    return _secret(settings.github_app_private_key) or (s.private_key if (s := stored()) and s.private_key else None)


def client_id() -> str | None:
    return settings.github_app_client_id or (s.client_id if (s := stored()) else None)


def client_secret() -> str | None:
    return _secret(settings.github_app_client_secret) or (s.client_secret if (s := stored()) else None)


def webhook_secret() -> str | None:
    return _secret(settings.github_app_webhook_secret) or (s.webhook_secret if (s := stored()) else None)


def slug() -> str | None:
    return settings.github_app_slug or (s.slug if (s := stored()) else None)


def env_credentials_set() -> bool:
    """Whether the environment already supplies the App's id or private key (it then takes precedence, so a
    manifest-created App could never be used)."""
    return bool(settings.github_app_id or _secret(settings.github_app_private_key))


def source() -> str:
    """Where the App's id and key come from: "env", "database" or "none"."""
    if env_credentials_set():
        return "env"
    return "database" if stored() is not None else "none"


def save(
    db: Session,
    *,
    app_id: str,
    slug: str | None,
    client_id: str | None,
    client_secret: str | None,
    webhook_secret: str | None,
    private_key: str,
    created_by_user_id: int | None,
) -> None:
    """Store (replacing any previous row) the credentials GitHub returned for a manifest-created App. The caller
    commits, so the audit row can go in the same transaction."""
    key = settings.job_secret_key.get_secret_value()

    def enc(value: str | None) -> str | None:
        return encrypt_job_token(value, key) if value else None

    row = db.get(GitHubAppCredentials, 1)
    if row is None:
        row = GitHubAppCredentials(id=1)
        db.add(row)
    row.app_id = app_id
    row.slug = slug
    row.client_id = client_id
    row.client_secret_enc = enc(client_secret)
    row.webhook_secret_enc = enc(webhook_secret)
    row.private_key_enc = encrypt_job_token(private_key, key)
    row.created_by_user_id = created_by_user_id
    db.flush()
    clear_cache()
