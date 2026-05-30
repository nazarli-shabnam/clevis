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

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
