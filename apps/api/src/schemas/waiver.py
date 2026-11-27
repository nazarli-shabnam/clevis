from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _stripped_not_blank(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("must not be blank")
    return v


class WaiverRequestIn(BaseModel):
    """What a member asks for: the fields of a waiver except who owns it, which the approving admin settles."""

    check_id: str = Field(min_length=1, max_length=100)
    # None / empty = the whole org; otherwise only these repositories (plain names, not owner/name).
    repos: list[str] | None = Field(default=None, max_length=50)
    reason: str = Field(min_length=1, max_length=500)
    expires_at: datetime

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        return _stripped_not_blank(v)

    @field_validator("repos")
    @classmethod
    def _clean_repos(cls, v: list[str] | None) -> list[str] | None:
        if not v:
            return None
        cleaned = []
        for name in v:
            name = name.strip()
            if "/" in name:
                raise ValueError("use plain repository names, without the owner prefix")
            if not name or len(name) > 100:
                raise ValueError("repository names must be 1-100 characters")
            if name.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(name)
        return cleaned


class WaiverIn(WaiverRequestIn):
    owner: str = Field(min_length=1, max_length=100)

    @field_validator("owner")
    @classmethod
    def _owner_not_blank(cls, v: str) -> str:
        return _stripped_not_blank(v)


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


class WaivableCheckOut(BaseModel):
    id: str
    title: str
    # True when the check never names failing repos, so only an org-wide waiver is accepted for it.
    org_wide_only: bool


class WaiverRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    check_id: str
    repos: list[str] | None
    reason: str
    requested_expires_at: datetime
    requested_by: str
    status: str
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    waiver_id: int | None
    created_at: datetime


class WaiverRequestApprove(BaseModel):
    """Optional adjustments when approving: who owns the waiver (default: the requester) and its expiry (default:
    the requested one)."""

    owner: str | None = Field(default=None, min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("owner")
    @classmethod
    def _owner_not_blank(cls, v: str | None) -> str | None:
        return None if v is None else _stripped_not_blank(v)


class WaiverRequestDeny(BaseModel):
    note: str | None = Field(default=None, max_length=500)
