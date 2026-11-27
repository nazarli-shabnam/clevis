"""Check waivers (accepted-risk exceptions) for an org. Members read and can request one; org admins create, edit,
revoke and decide requests.

Every change is audited. Creating or editing one does not rescan: the new score appears on the next
scan, and the stored scan keeps the waivers that applied when it ran."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import CheckWaiver, WaiverRequest, get_db
from src.core.path_params import RowId
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, waiver_repo, waiver_request_repo
from src.schemas.waiver import (
    WaivableCheckOut,
    WaiverIn,
    WaiverOut,
    WaiverRequestApprove,
    WaiverRequestDeny,
    WaiverRequestIn,
    WaiverRequestOut,
)
from src.services import waivers as waiver_service

router = APIRouter()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _out(w: CheckWaiver, now: datetime) -> WaiverOut:
    out = WaiverOut.model_validate(w)
    out.active = _aware(w.expires_at) > now
    return out


def _validate(body: WaiverIn | WaiverRequestIn, now: datetime) -> None:
    if body.check_id not in waiver_service.KNOWN_CHECK_IDS:
        raise HTTPException(status_code=422, detail="Unknown check")
    if body.repos and body.check_id in waiver_service.ORG_WIDE_ONLY_CHECK_IDS:
        raise HTTPException(
            status_code=422,
            detail="This check does not report which repositories fail, so it can only be waived for the whole organization; leave repositories empty",
        )
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


@router.get("/orgs/{org_login}/waivable-checks", response_model=list[WaivableCheckOut])
def list_waivable_checks(ctx: OrgContext = Depends(require_org_role(min_role="member"))):
    return waiver_service.waivable_checks()


@router.get("/orgs/{org_login}/waivers", response_model=list[WaiverOut])
def list_waivers(
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    return [_out(w, now) for w in waiver_repo.list_for_tenant(db, ctx.org.tenant_id)]


def _create_waiver(db: Session, ctx: OrgContext, user: UserOut, body: WaiverIn, now: datetime) -> CheckWaiver:
    """Validate, cap-check and insert a waiver and its audit row (not committed). Shared by an admin creating one
    directly and by approving a member's request."""
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
        expiry_warning_stage=waiver_service.warning_stage(_aware(body.expires_at), now),
    )
    audit_repo.write(
        db, actor=user.email, action="waiver.create", target=ctx.org.github_login,
        payload=_payload(waiver), tenant_id=ctx.org.tenant_id, commit=False,
    )
    return waiver


@router.post("/orgs/{org_login}/waivers", response_model=WaiverOut, status_code=status.HTTP_201_CREATED)
def create_waiver(
    body: WaiverIn,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    waiver = _create_waiver(db, ctx, user, body, now)
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
    waiver_id: RowId,
    body: WaiverIn,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    _validate(body, now)
    waiver = _get_or_404(db, ctx, waiver_id)
    # Re-read under lock: the expiry sweep may have advanced the stage since the load, and an assignment equal to
    # the stale loaded value would be left out of the UPDATE, so the renewal would keep the sweep's stage.
    db.refresh(waiver, with_for_update=True)
    before = _payload(waiver)
    old_expiry = _aware(waiver.expires_at)
    waiver.check_id, waiver.repos, waiver.reason = body.check_id, body.repos, body.reason
    waiver.owner, waiver.expires_at = body.owner, _aware(body.expires_at)
    # A renewed waiver can lapse (and be announced) again.
    waiver.expiry_noted_at = None
    # It is also warned about again before it does, but only when the expiry actually moved: an edit that leaves
    # the date alone (just a reason or owner) must not advance the stage past a warning the sweep has not sent yet.
    if waiver.expires_at != old_expiry:
        waiver.expiry_warning_stage = waiver_service.warning_stage(waiver.expires_at, now)
    audit_repo.write(
        db, actor=user.email, action="waiver.update", target=ctx.org.github_login,
        payload={"before": before, "after": _payload(waiver)}, tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
    db.refresh(waiver)
    return _out(waiver, now)


@router.delete("/orgs/{org_login}/waivers/{waiver_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_waiver(
    waiver_id: RowId,
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


# --- waiver requests (#1274): a member asks, an org admin decides ---

# Bounds so one account (or a runaway client) cannot fill the admins' queue.
_MAX_PENDING_PER_USER = 5
_MAX_PENDING_PER_TENANT = 50


def _request_payload(r: WaiverRequest) -> dict:
    return {
        "request_id": r.id,
        "check_id": r.check_id,
        "repos": r.repos,
        "reason": r.reason,
        "requested_by": r.requested_by,
        "requested_expires_at": _aware(r.requested_expires_at).isoformat(),
    }


@router.post("/orgs/{org_login}/waiver-requests", response_model=WaiverRequestOut, status_code=status.HTTP_201_CREATED)
def request_waiver(
    body: WaiverRequestIn,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Ask the org admins to waive a check. Nothing is waived until an admin approves."""
    now = datetime.now(timezone.utc)
    _validate(body, now)
    tenant_id = ctx.org.tenant_id
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('waiver_requests'), :t)"), {"t": tenant_id})
    if waiver_request_repo.count_pending(db, tenant_id, requested_by_user_id=user.id) >= _MAX_PENDING_PER_USER:
        raise HTTPException(status_code=409, detail=f"You already have {_MAX_PENDING_PER_USER} requests waiting for an admin")
    if waiver_request_repo.count_pending(db, tenant_id) >= _MAX_PENDING_PER_TENANT:
        raise HTTPException(status_code=409, detail="Too many waiver requests are waiting for an admin; try again later")
    request = waiver_request_repo.create(
        db,
        tenant_id=tenant_id,
        check_id=body.check_id,
        repos=body.repos,
        reason=body.reason,
        requested_expires_at=_aware(body.expires_at),
        requested_by=user.email,
        requested_by_user_id=user.id,
    )
    audit_repo.write(
        db, actor=user.email, action="waiver.request", target=ctx.org.github_login,
        payload=_request_payload(request), tenant_id=tenant_id, commit=False,
    )
    db.commit()
    db.refresh(request)
    return request


@router.get("/orgs/{org_login}/waiver-requests", response_model=list[WaiverRequestOut])
def list_waiver_requests(
    status_filter: Literal["pending", "approved", "denied"] | None = Query(default=None, alias="status"),
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Admins see every request; a member sees only their own."""
    own_only = None if ctx.membership.role == "admin" else user.id
    return waiver_request_repo.list_for_tenant(db, ctx.org.tenant_id, status=status_filter, requested_by_user_id=own_only)


def _pending_request_or_error(db: Session, ctx: OrgContext, request_id: int) -> WaiverRequest:
    request = waiver_request_repo.get(db, ctx.org.tenant_id, request_id, lock=True)
    if request is None:
        raise HTTPException(status_code=404, detail="Waiver request not found")
    if request.status != "pending":
        raise HTTPException(status_code=409, detail=f"This request was already {request.status}")
    return request


@router.post("/orgs/{org_login}/waiver-requests/{request_id}/approve", response_model=WaiverRequestOut)
def approve_waiver_request(
    request_id: RowId,
    body: WaiverRequestApprove | None = None,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Create the waiver the request asked for (the admin may change its owner and expiry) and close the request."""
    now = datetime.now(timezone.utc)
    request = _pending_request_or_error(db, ctx, request_id)
    adjust = body or WaiverRequestApprove()
    expires_at = _aware(adjust.expires_at or request.requested_expires_at)
    if expires_at <= now:
        raise HTTPException(status_code=422, detail="The requested expiry has passed; choose a new expiry date to approve it")
    waiver = _create_waiver(
        db,
        ctx,
        user,
        WaiverIn(
            check_id=request.check_id,
            repos=request.repos,
            reason=request.reason,
            # An email can be longer than an owner name may be.
            owner=adjust.owner or request.requested_by[:100],
            expires_at=expires_at,
        ),
        now,
    )
    request.status, request.decided_by, request.decided_at, request.waiver_id = "approved", user.email, now, waiver.id
    audit_repo.write(
        db, actor=user.email, action="waiver.request_approve", target=ctx.org.github_login,
        payload={**_request_payload(request), "waiver_id": waiver.id}, tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
    db.refresh(request)
    return request


@router.post("/orgs/{org_login}/waiver-requests/{request_id}/deny", response_model=WaiverRequestOut)
def deny_waiver_request(
    request_id: RowId,
    body: WaiverRequestDeny | None = None,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    request = _pending_request_or_error(db, ctx, request_id)
    note = (body.note.strip() if body and body.note else None) or None
    request.status, request.decided_by, request.decided_at, request.decision_note = "denied", user.email, now, note
    audit_repo.write(
        db, actor=user.email, action="waiver.request_deny", target=ctx.org.github_login,
        payload={**_request_payload(request), "note": note}, tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
    db.refresh(request)
    return request
