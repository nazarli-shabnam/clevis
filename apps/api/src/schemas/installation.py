from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyncInstallationsInput(BaseModel):
    auth_mode: str = "app"
    account_login: str
    account_type: str = "Organization"
    installation_id: int | None = None


class SyncInstallationsResponse(BaseModel):
    synced: bool
    token_ref: str


class InstallationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_login: str
    account_type: str
    installation_id: int | None
    created_at: datetime
