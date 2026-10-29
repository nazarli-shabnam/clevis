"""Org-scoped alert destinations (Slack, Teams, generic signed webhook). Org admins only."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token, encrypt_job_token
from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import NotificationDestination, get_db
from src.core.rate_limit import check_account_rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, notification_repo
from src.schemas.notification import DestinationCreate, DestinationOut, TestSendResult
from src.services import email, notifications

router = APIRouter()

_MAX_DESTINATIONS_PER_ORG = 10
_TEST_SENDS_PER_MINUTE = 5


def _out(dest: NotificationDestination) -> DestinationOut:
    out = DestinationOut.model_validate(dest)
    out.signed = dest.encrypted_secret is not None
    if dest.kind == "email":
        # Admin-only endpoint: the addresses are shown so an admin can see who receives the alerts.
        try:
            key = settings.job_secret_key.get_secret_value()
            out.recipients = json.loads(decrypt_job_token(dest.encrypted_url, key))
        except Exception:
            out.recipients = []
    return out


@router.get("/orgs/{org_login}/notification-destinations", response_model=list[DestinationOut])
def list_destinations(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    return [_out(d) for d in notification_repo.list_for_tenant(db, ctx.org.tenant_id)]


def _email_recipients_or_422(db: Session, ctx: OrgContext, body: DestinationCreate) -> str:
    """JSON list of the recipients to store, after checking SMTP is set up and every address belongs to a
    verified member of this org (so the instance's mail server can't be aimed at arbitrary addresses)."""
    if not email.is_configured():
        raise HTTPException(
            status_code=422,
            detail="Email alerts need SMTP: an operator must set SMTP_HOST and SMTP_FROM on this instance.",
        )
    wanted = sorted({str(r).lower() for r in body.recipients or []})
    allowed = notifications.verified_member_emails(db, ctx.org.tenant_id)
    unknown = [r for r in wanted if r not in allowed]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Recipients must be verified members of this organization: " + ", ".join(unknown),
        )
    return json.dumps(wanted)


@router.post(
    "/orgs/{org_login}/notification-destinations", response_model=DestinationOut, status_code=status.HTTP_201_CREATED
)
def create_destination(
    body: DestinationCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if body.kind == "email":
        stored = _email_recipients_or_422(db, ctx, body)
    else:
        url = body.url.get_secret_value()
        try:
            notifications.validate_destination_url(url)
        except notifications.UnsafeDestinationURL as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        stored = url
    if notification_repo.count_for_tenant(db, ctx.org.tenant_id) >= _MAX_DESTINATIONS_PER_ORG:
        raise HTTPException(status_code=409, detail=f"At most {_MAX_DESTINATIONS_PER_ORG} destinations per organization")

    key = settings.job_secret_key.get_secret_value()
    secret = body.secret.get_secret_value() if body.secret and body.kind == "generic" else None
    dest = notification_repo.create(
        db,
        tenant_id=ctx.org.tenant_id,
        kind=body.kind,
        name=body.name,
        encrypted_url=encrypt_job_token(stored, key),
        encrypted_secret=encrypt_job_token(secret, key) if secret else None,
        events=list(body.events),
        min_score_drop=body.min_score_drop,
    )
    audit_repo.write(
        db,
        actor=user.email,
        action="notification.destination_created",
        target=ctx.org.github_login,
        payload={
            "destination_id": dest.id,
            "kind": body.kind,
            "name": body.name,
            "events": list(body.events),
            **({"recipient_count": len(json.loads(stored))} if body.kind == "email" else {}),
        },
        tenant_id=ctx.org.tenant_id,
        commit=False,
    )
    db.commit()
    db.refresh(dest)
    return _out(dest)


def _get_or_404(db: Session, ctx: OrgContext, destination_id: int) -> NotificationDestination:
    dest = notification_repo.get(db, ctx.org.tenant_id, destination_id)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    return dest


@router.delete("/orgs/{org_login}/notification-destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_destination(
    destination_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    dest = _get_or_404(db, ctx, destination_id)
    notification_repo.delete(db, dest)
    audit_repo.write(
        db,
        actor=user.email,
        action="notification.destination_deleted",
        target=ctx.org.github_login,
        payload={"destination_id": destination_id, "kind": dest.kind, "name": dest.name},
        tenant_id=ctx.org.tenant_id,
        commit=False,
    )
    db.commit()


@router.post("/orgs/{org_login}/notification-destinations/{destination_id}/test", response_model=TestSendResult)
def test_destination(
    destination_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    dest = _get_or_404(db, ctx, destination_id)
    # Each send makes the server call an admin-chosen URL; cap it so test-send can't be used as a scanner.
    check_account_rate_limit(f"notification-test:{ctx.org.tenant_id}", max_requests=_TEST_SENDS_PER_MINUTE)
    ok, detail = notifications.send(
        dest,
        "test",
        f"Clevis test message for {ctx.org.github_login}: this destination is working.",
        {"org": ctx.org.github_login},
        email_allowed=notifications.allowed_emails_for(db, ctx.org.tenant_id, [dest]),
    )
    audit_repo.write(
        db,
        actor=user.email,
        action="notification.test_sent",
        target=ctx.org.github_login,
        payload={"destination_id": destination_id, "kind": dest.kind, "ok": ok, "detail": detail},
        tenant_id=ctx.org.tenant_id,
    )
    # The target's status code or failure reason goes to the audit log only: echoing it would let an admin
    # probe internal-looking names by response.
    return TestSendResult(ok=ok, detail="Delivered" if ok else "Delivery failed; see the audit log")
