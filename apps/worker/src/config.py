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
    # Secrets only — github_api_base and worker_poll_seconds are read from app_config table.
    database_url: SecretStr
    job_secret_key: SecretStr

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
