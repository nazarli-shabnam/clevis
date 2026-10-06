"""Org-scoped alert destinations (Slack, Teams, generic signed webhook). Org admins only."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core._crypto import encrypt_job_token
from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import NotificationDestination, get_db
from src.core.rate_limit import check_account_rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, notification_repo
from src.schemas.notification import DestinationCreate, DestinationOut, TestSendResult
from src.services import notifications

router = APIRouter()

_MAX_DESTINATIONS_PER_ORG = 10
_TEST_SENDS_PER_MINUTE = 5


def _out(dest: NotificationDestination) -> DestinationOut:
    out = DestinationOut.model_validate(dest)
    out.signed = dest.encrypted_secret is not None
    return out


@router.get("/orgs/{org_login}/notification-destinations", response_model=list[DestinationOut])
def list_destinations(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    return [_out(d) for d in notification_repo.list_for_tenant(db, ctx.org.tenant_id)]


@router.post(
    "/orgs/{org_login}/notification-destinations", response_model=DestinationOut, status_code=status.HTTP_201_CREATED
)
def create_destination(
    body: DestinationCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    url = body.url.get_secret_value()
    try:
        notifications.validate_destination_url(url)
    except notifications.UnsafeDestinationURL as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if notification_repo.count_for_tenant(db, ctx.org.tenant_id) >= _MAX_DESTINATIONS_PER_ORG:
        raise HTTPException(status_code=409, detail=f"At most {_MAX_DESTINATIONS_PER_ORG} destinations per organization")

    key = settings.job_secret_key.get_secret_value()
    secret = body.secret.get_secret_value() if body.secret and body.kind == "generic" else None
    dest = notification_repo.create(
        db,
        tenant_id=ctx.org.tenant_id,
        kind=body.kind,
        name=body.name,
        encrypted_url=encrypt_job_token(url, key),
        encrypted_secret=encrypt_job_token(secret, key) if secret else None,
        events=list(body.events),
        min_score_drop=body.min_score_drop,
    )
    audit_repo.write(
        db,
        actor=user.email,
        action="notification.destination_created",
        target=ctx.org.github_login,
        payload={"destination_id": dest.id, "kind": body.kind, "name": body.name, "events": list(body.events)},
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
        dest, "test", f"Clevis test message for {ctx.org.github_login}: this destination is working.", {"org": ctx.org.github_login}
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
