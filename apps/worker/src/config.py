from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parent
while _ROOT != _ROOT.parent:
    if (_ROOT / ".env").exists():
        break
    _ROOT = _ROOT.parent


class Settings(BaseSettings):
    database_url: SecretStr
    job_secret_key: SecretStr
    github_api_base: str
    worker_poll_seconds: int

    model_config = SettingsConfigDict(env_file=str(_ROOT / ".env"), extra="ignore")


settings = Settings()
