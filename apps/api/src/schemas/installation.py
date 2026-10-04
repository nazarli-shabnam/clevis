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


class InstallationLookupOut(BaseModel):
    account_login: str
    account_type: str


class BlockedFeatureOut(BaseModel):
    feature: str
    label: str
    missing: dict[str, str]


class AutomationPermissionOut(BaseModel):
    """One optional automation, the permissions it needs, and which of those the install lacks."""

    feature: str
    label: str
    required: dict[str, str]
    # Empty both when everything is granted and when permissions were never synced
    # (`permissions_synced_at` is None) -- the UI tells those apart off the timestamp.
    missing: dict[str, str] = {}


class InstallationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_login: str
    account_type: str
    installation_id: int | None
    created_at: datetime
    # `permissions_synced_at` is None for installs that predate permission tracking --
    # `blocked_features` is empty too, so the UI shows "not yet checked" not a false "all good".
    permissions_synced_at: datetime | None = None
    blocked_features: list[BlockedFeatureOut] = []
    # Every optional automation (blocked or not), for the Permissions doctor view.
    automations: list[AutomationPermissionOut] = []
