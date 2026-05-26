from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.repositories import installation_repo
from src.schemas.installation import SyncInstallationsInput, SyncInstallationsResponse

router = APIRouter()


@router.post("/github/app/installations/sync", response_model=SyncInstallationsResponse)
def sync_installation(
    payload: SyncInstallationsInput,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
):
    row = installation_repo.create(
        db,
        account_login=payload.account_login,
        account_type=payload.account_type,
        auth_mode=payload.auth_mode,
        installation_id=payload.installation_id,
    )
    return {"synced": True, "token_ref": row.token_ref}
