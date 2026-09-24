from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_root = Path(__file__).resolve().parent
_env_file: str | None = None
while _root != _root.parent:
    candidate = _root / ".env"
    if candidate.exists():
        _env_file = str(candidate)
        break
    _root = _root.parent


class Settings(BaseSettings):
    # Required secrets — no defaults.
    database_url: SecretStr
    job_secret_key: SecretStr   # Fernet key for saved-token + job token encryption
    auth_secret: SecretStr      # JWT signing secret
    redis_url: SecretStr        # SecretStr: a production URL may embed AUTH credentials

    # Deploy-time config. cors_origins (a security boundary) and github_api_base (where tokens
    # are sent) are read at startup, never runtime-editable.
    github_api_base: str = "https://api.github.com"
    cors_origins: list[str] = ["http://localhost:3000"]  # CORS_ORIGINS env value is parsed as JSON

    # GitHub App: optional; the github_app service raises a clear error if used while unconfigured.
    github_app_id: str | None = None
    github_app_client_id: str | None = None
    github_app_private_key: SecretStr | None = None
    github_app_client_secret: SecretStr | None = None
    github_app_webhook_secret: SecretStr | None = None

    # Session cookie defaults are production-safe (HTTPS). Set session_cookie_secure=false for local
    # http, session_cookie_samesite="none" when the UI and API are on different sites.
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"
    session_cookie_domain: str | None = None

    # SMTP: optional; if unset, register() still creates the account, it just stays unverified.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
