from pydantic import BaseModel


class CacheListInput(BaseModel):
    token: str


class CacheClearInput(BaseModel):
    token: str
    actor: str
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
