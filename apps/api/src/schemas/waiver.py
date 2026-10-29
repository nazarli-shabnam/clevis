from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WaiverIn(BaseModel):
    check_id: str = Field(min_length=1, max_length=100)
    # None / empty = the whole org; otherwise only these repositories (plain names, not owner/name).
    repos: list[str] | None = Field(default=None, max_length=50)
    reason: str = Field(min_length=1, max_length=500)
    owner: str = Field(min_length=1, max_length=100)
    expires_at: datetime

    @field_validator("reason", "owner")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("repos")
    @classmethod
    def _clean_repos(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return None
        cleaned = []
        for name in v:
            name = name.strip().split("/")[-1]
            if not name or len(name) > 100:
                raise ValueError("repository names must be 1-100 characters")
            if name.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(name)
        return cleaned


class WaiverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    check_id: str
    repos: list[str] | None
    reason: str
    owner: str
    expires_at: datetime
    created_by: str
    created_at: datetime
    # Derived: an expired waiver is kept (for the record and to renew) but no longer excludes its check.
    active: bool = True
