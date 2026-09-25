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
from src.core.auth import UserOut, require_workspace_admin
from src.core.config import settings
from src.core.db import SavedToken, get_db
from src.repositories import audit_repo, org_repo, tenant_repo
from src.services import org_provisioning

router = APIRouter()


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


@router.get("", response_model=list[TokenMeta])
def list_tokens(
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_workspace_admin),
) -> list[TokenMeta]:
    """Return metadata for all saved tokens (never the raw token). Workspace admin only."""
    rows = db.query(SavedToken).order_by(SavedToken.org).all()
    return [TokenMeta.model_validate(r) for r in rows]


@router.put("/{org}", response_model=TokenMeta)
def upsert_token(
    org: str,
    body: UpsertTokenRequest,
    db: Session = Depends(get_db),
    user: UserOut = Depends(require_workspace_admin),
) -> TokenMeta:
    """Save or update the token for an org (encrypted at rest). Workspace admin only.

    Also best-effort connects the org to Clevis using the pasted PAT so the org-scoped
    dashboard pages work afterwards, not just Overview.
    """
    encrypted = encrypt_job_token(
        body.token.get_secret_value(),
        settings.job_secret_key.get_secret_value(),
    )
    # Best-effort tenant_id resolution: this legacy fallback lets an admin save a PAT for
    # any org string, including one Clevis has never connected -- if a matching Org row
    # exists, link its tenant now so RLS's WITH CHECK can accept the write; if not,
    # tenant_id stays NULL.
    existing_org = org_repo.get_by_login_ci(db, org)

    # Skip the auto-connect path (a paginated GitHub crawl) only when the caller already
    # has an *admin* membership for this org; a missing/stale row still goes through it.
    nothing_to_do = False
    if existing_org is not None:
        existing_org = org_repo.ensure_tenant_linked(db, existing_org)
        membership = tenant_repo.get_membership(db, existing_org.tenant_id, user.id)
        nothing_to_do = membership is not None and membership.role == "admin"

    # If this admin can't already reach the org, try to establish the Org + admin
    # membership from the pasted PAT -- otherwise every /orgs/{org}/... page 404s.
    if not nothing_to_do:
        connected_org = org_provisioning.connect_admin_org_from_token(
            db, user, org, body.token.get_secret_value()
        )
        if connected_org is not None:
            existing_org = connected_org

    tenant_id = existing_org.tenant_id if existing_org else None

    row = db.query(SavedToken).filter_by(org=org).first()
    if row:
        row.encrypted_token = encrypted
        row.label = body.label
        if row.tenant_id is None:
            row.tenant_id = tenant_id
    else:
        row = SavedToken(org=org, label=body.label, encrypted_token=encrypted, tenant_id=tenant_id)
        db.add(row)
    audit_repo.write(
        db, user.email, "token.save", org, {"label": body.label},
        tenant_id=row.tenant_id or tenant_repo.ensure_personal_tenant(db, user.id, commit=False).id,
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return TokenMeta.model_validate(row)


@router.post("/resolve", response_model=VerifyTokenResponse)
def resolve_token(
    body: VerifyTokenRequest,
    db: Session = Depends(get_db),
    user: UserOut = Depends(require_workspace_admin),
) -> VerifyTokenResponse:
    """Decrypt and return the saved token for an org. Returns raw secret — workspace admin only."""
    row = db.query(SavedToken).filter_by(org=body.org).first()
    if not row:
        raise HTTPException(status_code=404, detail="No saved token for this org")
    raw = decrypt_job_token(
        row.encrypted_token,
        settings.job_secret_key.get_secret_value(),
    )
    # row.tenant_id is best-effort -- falls back to the acting admin's own personal tenant
    # when the saved token predates a matching Org, so this write always has a real tenant_id.
    tenant_id = row.tenant_id or tenant_repo.ensure_personal_tenant(db, user.id).id
    audit_repo.write(db, user.email, "token.resolve", body.org, {}, tenant_id=tenant_id)
    return VerifyTokenResponse(token=raw)


@router.delete("/{org}", status_code=204)
def delete_token(
    org: str,
    db: Session = Depends(get_db),
    user: UserOut = Depends(require_workspace_admin),
) -> None:
    """Remove a saved token. Workspace admin only."""
    row = db.query(SavedToken).filter_by(org=org).first()
    if not row:
        raise HTTPException(status_code=404, detail="No saved token for this org")
    tenant_id = row.tenant_id or tenant_repo.ensure_personal_tenant(db, user.id, commit=False).id
    db.delete(row)
    audit_repo.write(db, user.email, "token.delete", org, {}, tenant_id=tenant_id, commit=False)
    db.commit()
