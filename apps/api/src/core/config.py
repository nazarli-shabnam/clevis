from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).parents[4]  # apps/api/src/core/ -> repo root


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://clevis:clevis@localhost:5432/clevis"
    github_api_base: str = "https://api.github.com"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    default_rbac_role: str = "viewer"
    worker_poll_seconds: int = 5

    model_config = SettingsConfigDict(env_file=str(_ROOT / ".env"), extra="ignore")


settings = Settings()
