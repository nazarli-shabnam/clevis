from pydantic import BaseModel, Field, SecretStr


class CacheListInput(BaseModel):
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this owner (see src.services.token_resolution).
    token: SecretStr | None = None


class CacheClearInput(BaseModel):
    token: SecretStr | None = None
    # Bounded so a caller can't bloat the jobs/audit_logs payload columns (both
    # unbounded Text) with an arbitrarily large value. 512 matches GitHub's own Actions
    # cache key limit; 255 is a generous cap for a git ref name.
    key: str | None = Field(default=None, max_length=512)
    ref: str | None = Field(default=None, max_length=255)
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
