from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_owner
from src.core.db import get_db
from src.repositories import installation_repo
from src.schemas.installation import (
    InstallationOut,
    SyncInstallationsInput,
    SyncInstallationsResponse,
)

router = APIRouter()


@router.get("/github/app/installations", response_model=list[InstallationOut])
def list_installations(
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_owner),
):
    """Organizations that have installed the Clevis GitHub App (the 'Connected Orgs' list)."""
    return installation_repo.list_all(db)


@router.post("/github/app/installations/sync", response_model=SyncInstallationsResponse)
def sync_installation(
    payload: SyncInstallationsInput,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_owner),
):
    row = installation_repo.create(
        db,
        account_login=payload.account_login,
        account_type=payload.account_type,
        auth_mode=payload.auth_mode,
        installation_id=payload.installation_id,
    )
    return {"synced": True, "token_ref": row.token_ref}
