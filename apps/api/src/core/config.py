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

    # Deploy-time config with safe defaults. Override via env per environment/install.
    # cors_origins is a security boundary read once at startup; github_api_base is where
    # GitHub tokens are sent — both are set at deploy time, not editable at runtime.
    github_api_base: str = "https://api.github.com"
    cors_origins: list[str] = ["http://localhost:3000"]  # CORS_ORIGINS env value is parsed as JSON

    # GitHub App (S1 — SaaS auth). Optional until the App is registered; the github_app
    # service raises a clear error if used while unconfigured. No secret defaults are baked in.
    # app_id / client_id are public identifiers; the rest are secrets, hence SecretStr.
    github_app_id: str | None = None
    github_app_client_id: str | None = None
    github_app_private_key: SecretStr | None = None
    github_app_client_secret: SecretStr | None = None
    github_app_webhook_secret: SecretStr | None = None

    # httpOnly session cookie (set on GitHub OAuth callback + login). Defaults are safe for
    # production (HTTPS); set session_cookie_secure=false for local http dev, and
    # session_cookie_samesite="none" when the UI and API are on different sites.
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"
    session_cookie_domain: str | None = None

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
