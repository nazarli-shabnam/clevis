from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://clevis:clevis@localhost:5432/clevis"
    github_api_base: str = "https://api.github.com"
    worker_poll_seconds: int = 5

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
