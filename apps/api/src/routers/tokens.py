"""
Saved-token CRUD.

Tokens are Fernet-encrypted at rest using JOB_SECRET_KEY (same key as jobs).
The GET /tokens endpoint intentionally never returns raw tokens — only metadata
(org name, label, created_at). The UI uses PUT to upsert and DELETE to remove.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token, encrypt_job_token
from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import SavedToken, get_db

router = APIRouter()


# ── schemas ────────────────────────────────────────────────────────────────

class TokenMeta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    org: str
    label: str | None
    created_at: datetime
    updated_at: datetime


class UpsertTokenRequest(BaseModel):
    token: SecretStr
    label: str | None = None


class VerifyTokenRequest(BaseModel):
    org: str


class VerifyTokenResponse(BaseModel):
    token: str


# ── routes ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[TokenMeta])
def list_tokens(
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
) -> list[TokenMeta]:
    """Return metadata for all saved tokens (never the raw token). Requires authentication."""
    rows = db.query(SavedToken).order_by(SavedToken.org).all()
    return [TokenMeta.model_validate(r) for r in rows]


@router.put("/{org}", response_model=TokenMeta)
def upsert_token(
    org: str,
    body: UpsertTokenRequest,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
) -> TokenMeta:
    """Save or update the token for an org (encrypted at rest). Requires authentication."""
    encrypted = encrypt_job_token(
        body.token.get_secret_value(),
        settings.job_secret_key.get_secret_value(),
    )
    row = db.query(SavedToken).filter_by(org=org).first()
    if row:
        row.encrypted_token = encrypted
        row.label = body.label
    else:
        row = SavedToken(org=org, label=body.label, encrypted_token=encrypted)
        db.add(row)
    db.commit()
    db.refresh(row)
    return TokenMeta.model_validate(row)


@router.post("/resolve", response_model=VerifyTokenResponse)
def resolve_token(
    body: VerifyTokenRequest,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
) -> VerifyTokenResponse:
    """Decrypt and return the saved token for an org. Returns raw secret — requires authentication."""
    row = db.query(SavedToken).filter_by(org=body.org).first()
    if not row:
        raise HTTPException(status_code=404, detail="No saved token for this org")
    raw = decrypt_job_token(
        row.encrypted_token,
        settings.job_secret_key.get_secret_value(),
    )
    return VerifyTokenResponse(token=raw)


@router.delete("/{org}", status_code=204)
def delete_token(
    org: str,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
) -> None:
    """Remove a saved token. Requires authentication."""
    row = db.query(SavedToken).filter_by(org=org).first()
    if not row:
        raise HTTPException(status_code=404, detail="No saved token for this org")
    db.delete(row)
    db.commit()
