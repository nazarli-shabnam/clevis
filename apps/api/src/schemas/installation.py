from pydantic import BaseModel


class SyncInstallationsInput(BaseModel):
    token: str
    auth_mode: str = "app"
    account_login: str
    account_type: str = "Organization"
    installation_id: int | None = None


class SyncInstallationsResponse(BaseModel):
    synced: bool
    token_ref: str
