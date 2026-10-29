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
    # worker_poll_seconds lives in app_config so it can be tuned live.
    database_url: SecretStr
    job_secret_key: SecretStr
    github_api_base: str = "https://api.github.com"
    # SecretStr: a production URL may embed AUTH credentials.
    redis_url: SecretStr

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


_CONNECT_TIMEOUT_SECONDS = 5


def plain_db_url(url: str) -> str:
    """Strip the SQLAlchemy dialect prefix (psycopg.connect() expects plain postgresql://) and bound
    connect time.

    Without connect_timeout, psycopg.connect() can hang indefinitely on a black-holed host; every
    worker connection (job poll, event consumer) goes through this."""
    stripped = url.replace("postgresql+psycopg://", "postgresql://")
    sep = "&" if "?" in stripped else "?"
    return f"{stripped}{sep}connect_timeout={_CONNECT_TIMEOUT_SECONDS}"


settings = Settings()
