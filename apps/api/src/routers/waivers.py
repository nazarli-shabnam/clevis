"""Check waivers (accepted-risk exceptions) for an org. Members read; org admins create, edit and revoke.

Every change is audited. Creating or editing one does not rescan: the new score appears on the next
scan, and the stored scan keeps the waivers that applied when it ran."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import CheckWaiver, get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, waiver_repo
from src.schemas.waiver import WaiverIn, WaiverOut
from src.services import waivers as waiver_service

router = APIRouter()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _out(w: CheckWaiver, now: datetime) -> WaiverOut:
    out = WaiverOut.model_validate(w)
    out.active = _aware(w.expires_at) > now
    return out


def _validate(body: WaiverIn, now: datetime) -> None:
    if body.check_id not in waiver_service.KNOWN_CHECK_IDS:
        raise HTTPException(status_code=422, detail="Unknown check")
    expires = _aware(body.expires_at)
    if expires <= now:
        raise HTTPException(status_code=422, detail="The waiver must expire in the future")
    if expires > waiver_service.max_expiry(now):
        raise HTTPException(
            status_code=422, detail=f"A waiver can last at most {waiver_service.MAX_WAIVER_DAYS} days; renew it when it lapses"
        )


def _payload(w: CheckWaiver) -> dict:
    return {
        "waiver_id": w.id,
        "check_id": w.check_id,
        "repos": w.repos,
        "reason": w.reason,
        "owner": w.owner,
        "expires_at": _aware(w.expires_at).isoformat(),
    }


@router.get("/orgs/{org_login}/waivers", response_model=list[WaiverOut])
def list_waivers(
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    return [_out(w, now) for w in waiver_repo.list_for_tenant(db, ctx.org.tenant_id)]


@router.post("/orgs/{org_login}/waivers", response_model=WaiverOut, status_code=status.HTTP_201_CREATED)
def create_waiver(
    body: WaiverIn,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    _validate(body, now)
    # Serialize creations per tenant for this transaction, so two concurrent requests can't both read a
    # count under the cap and both insert past it.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('check_waivers'), :t)"), {"t": ctx.org.tenant_id})
    if waiver_repo.count_for_tenant(db, ctx.org.tenant_id) >= waiver_service.MAX_WAIVERS_PER_TENANT:
        raise HTTPException(
            status_code=409, detail=f"At most {waiver_service.MAX_WAIVERS_PER_TENANT} waivers per organization; revoke some first"
        )
    waiver = waiver_repo.create(
        db,
        tenant_id=ctx.org.tenant_id,
        check_id=body.check_id,
        repos=body.repos,
        reason=body.reason,
        owner=body.owner,
        expires_at=_aware(body.expires_at),
        created_by=user.email,
    )
    audit_repo.write(
        db, actor=user.email, action="waiver.create", target=ctx.org.github_login,
        payload=_payload(waiver), tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
    db.refresh(waiver)
    return _out(waiver, now)


def _get_or_404(db: Session, ctx: OrgContext, waiver_id: int) -> CheckWaiver:
    waiver = waiver_repo.get(db, ctx.org.tenant_id, waiver_id)
    if waiver is None:
        raise HTTPException(status_code=404, detail="Waiver not found")
    return waiver


@router.put("/orgs/{org_login}/waivers/{waiver_id}", response_model=WaiverOut)
def update_waiver(
    waiver_id: int,
    body: WaiverIn,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    _validate(body, now)
    waiver = _get_or_404(db, ctx, waiver_id)
    before = _payload(waiver)
    waiver.check_id, waiver.repos, waiver.reason = body.check_id, body.repos, body.reason
    waiver.owner, waiver.expires_at = body.owner, _aware(body.expires_at)
    # A renewed waiver can lapse (and be announced) again.
    waiver.expiry_noted_at = None
    audit_repo.write(
        db, actor=user.email, action="waiver.update", target=ctx.org.github_login,
        payload={"before": before, "after": _payload(waiver)}, tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
    db.refresh(waiver)
    return _out(waiver, now)


@router.delete("/orgs/{org_login}/waivers/{waiver_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_waiver(
    waiver_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    waiver = _get_or_404(db, ctx, waiver_id)
    payload = _payload(waiver)
    waiver_repo.delete(db, waiver)
    audit_repo.write(
        db, actor=user.email, action="waiver.revoke", target=ctx.org.github_login,
        payload=payload, tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
