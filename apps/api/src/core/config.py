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
    github_api_base: str = "https://api.github.com"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    default_rbac_role: str = "viewer"
    worker_poll_seconds: int = 5
    debug: bool = False

    model_config = SettingsConfigDict(env_file=str(_ROOT / ".env"), extra="ignore")


settings = Settings()
