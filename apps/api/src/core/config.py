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
    # The three required secrets — everything else lives in the app_config DB table.
    database_url: SecretStr
    job_secret_key: SecretStr   # Fernet key for saved-token + job token encryption
    auth_secret: SecretStr      # JWT signing secret

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
