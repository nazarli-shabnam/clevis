from pydantic import BaseModel, SecretStr


class CacheListInput(BaseModel):
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this owner (see src.services.token_resolution).
    token: SecretStr | None = None


class CacheClearInput(BaseModel):
    token: SecretStr | None = None
    key: str | None = None
    ref: str | None = None
    dry_run: bool = True


class CacheListResponse(BaseModel):
    repository: str
    total: int
    actions_caches: list[dict]


class CacheClearResponse(BaseModel):
    queued: bool
    dry_run: bool | None = None
    job_id: int | None = None
    message: str | None = None
