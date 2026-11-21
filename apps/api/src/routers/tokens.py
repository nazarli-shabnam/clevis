"""
Saved-token CRUD.

Tokens are Fernet-encrypted at rest using JOB_SECRET_KEY (same key as jobs).
The GET /tokens endpoint intentionally never returns raw tokens — only metadata
(org name, label, created_at). The UI uses PUT to upsert and DELETE to remove.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token, encrypt_job_token
from src.core.auth import UserOut, require_workspace_admin
from src.core.config import settings
from src.core.db import SavedToken, get_db
from src.core.path_params import Owner
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
    org: Owner


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


def _saved_token_variants(db: Session, org: str) -> list[SavedToken]:
    """Every saved-token row for ``org`` whatever its casing, the exact-case row first -- the order
    ``token_resolution`` reads it in. Rows saved before saves matched case-insensitively can exist side by side."""
    rows = db.query(SavedToken).filter(func.lower(SavedToken.org) == org.lower()).order_by(SavedToken.id).all()
    return sorted(rows, key=lambda r: r.org != org)


def _lock_org_tokens(db: Session, org: str) -> None:
    """Serialize saves and deletes for one org (logins are case-insensitive) until the transaction ends: a save
    and a delete that both touch the Acme/acme rows would otherwise lock them in opposite orders and deadlock."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"saved_token:{org.lower()}"})


def _find_saved_token(db: Session, org: str) -> SavedToken | None:
    """The saved token for ``org``: the exact-case row first, else a case-insensitive match, so a token saved
    as ``Acme`` is replaced (not duplicated) by a save for ``acme``."""
    return next(iter(_saved_token_variants(db, org)), None)


@router.put("/{org}", response_model=TokenMeta)
def upsert_token(
    org: Owner,
    body: UpsertTokenRequest,
    db: Session = Depends(get_db),
    user: UserOut = Depends(require_workspace_admin),
) -> TokenMeta:
    """Save or update the token for an org (encrypted at rest). Workspace admin only.

    Also best-effort connects the org to Clevis using the pasted PAT so the org-scoped
    dashboard pages work afterwards, not just Overview.
    """
    try:
        return _save_token(db, org, body, user)
    except IntegrityError:
        # Two saves for an org with no row both missed the lookup and both inserted; the loser hits the unique
        # constraint at commit. Start over: the winner's row now exists, so this pass takes the update path.
        db.rollback()
        return _save_token(db, org, body, user)


def _save_token(db: Session, org: str, body: UpsertTokenRequest, user: UserOut) -> TokenMeta:
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
    unlink_stale_tenant = False
    if existing_org is not None:
        existing_org = org_repo.ensure_tenant_linked(db, existing_org)
        membership = tenant_repo.get_membership(db, existing_org.tenant_id, user.id)
        nothing_to_do = membership is not None and membership.role == "admin"

    # If this admin can't already reach the org, try to establish the Org + admin
    # membership from the pasted PAT -- otherwise every /orgs/{org}/... page 404s.
    connected_org = None
    if not nothing_to_do:
        try:
            connected_org = org_provisioning.connect_admin_org_from_token(
                db, user, org, body.token.get_secret_value()
            )
        except org_repo.OrgIdentityMismatch:
            # The login-matched Org belongs to a different GitHub org now; don't file this PAT
            # under its tenant (including a row saved earlier, while the login was still theirs).
            existing_org = None
            connected_org = None
            unlink_stale_tenant = True
        if connected_org is not None:
            existing_org = connected_org

    # Link the token to a tenant only when this caller is already an admin of it or the PAT just proved
    # they are; an org matched by login alone may now belong to a different GitHub org.
    verified = nothing_to_do or connected_org is not None
    tenant_id = existing_org.tenant_id if existing_org is not None and verified else None

    _lock_org_tokens(db, org)
    row = _find_saved_token(db, org)
    if row:
        row.encrypted_token = encrypted
        row.label = body.label
        if unlink_stale_tenant:
            row.tenant_id = None
        elif row.tenant_id is None:
            row.tenant_id = tenant_id
    else:
        row = SavedToken(org=org, label=body.label, encrypted_token=encrypted, tenant_id=tenant_id)
        db.add(row)
    # A re-save is a rotation: casing duplicates left over from before saves matched case-insensitively would
    # keep the old credential stored (and, if exact-case, preferred), so they go with it.
    for stale in _saved_token_variants(db, org):
        if stale is not row:
            db.delete(stale)
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
    row = _find_saved_token(db, body.org)
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
    # Not Owner: a token saved before names were validated may have an identifier Owner rejects, and it must stay removable.
    org: Annotated[str, Path(min_length=1, max_length=255)],
    db: Session = Depends(get_db),
    user: UserOut = Depends(require_workspace_admin),
) -> None:
    """Remove a saved token. Workspace admin only."""
    _lock_org_tokens(db, org)
    rows = _saved_token_variants(db, org)
    if not rows:
        raise HTTPException(status_code=404, detail="No saved token for this org")
    row = rows[0]
    tenant_id = row.tenant_id or tenant_repo.ensure_personal_tenant(db, user.id, commit=False).id
    for r in rows:  # every casing variant: a leftover duplicate would keep a deleted credential resolvable
        db.delete(r)
    audit_repo.write(db, user.email, "token.delete", org, {}, tenant_id=tenant_id, commit=False)
    db.commit()
