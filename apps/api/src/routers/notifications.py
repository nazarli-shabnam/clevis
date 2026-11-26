"""Org-scoped alert destinations (Slack, Teams, generic signed webhook). Org admins only."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token, encrypt_job_token
from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import NotificationDestination, SecurityAlert, get_db
from src.core.path_params import RowId
from src.core.rate_limit import check_account_rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, notification_repo
from src.schemas.notification import (
    AnnounceOpenAlertsResult,
    DestinationCreate,
    DestinationOut,
    DestinationUpdate,
    TestSendResult,
)
from src.services import email, notifications

router = APIRouter()

_MAX_DESTINATIONS_PER_ORG = 10
_TEST_SENDS_PER_MINUTE = 5
_ANNOUNCES_PER_MINUTE = 2
# One message lists at most this many alerts (the first five by name); the rest are only counted.
_MAX_ANNOUNCED_ALERTS = 200


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


def _email_recipients_or_422(db: Session, ctx: OrgContext, recipients: list | None) -> str:
    """JSON list of the recipients to store, after checking SMTP is set up and every address belongs to a
    verified member of this org (so the instance's mail server can't be aimed at arbitrary addresses)."""
    if not email.is_configured():
        raise HTTPException(
            status_code=422,
            detail="Email alerts need SMTP: an operator must set SMTP_HOST and SMTP_FROM on this instance.",
        )
    wanted = sorted({str(r).lower() for r in recipients or []})
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
    if body.kind == "generic" and body.secret is not None and not body.secret.get_secret_value():
        # Same rule as PATCH: an empty secret would otherwise create an unsigned webhook that looks signed.
        raise HTTPException(status_code=422, detail="A signing secret cannot be empty")
    if body.kind == "email":
        stored = _email_recipients_or_422(db, ctx, body.recipients)
    else:
        url = body.url.get_secret_value()
        try:
            notifications.validate_destination_url(url)
        except notifications.UnsafeDestinationURL as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        stored = url
    # Serialize creations per tenant for this transaction, so two concurrent requests can't both read a
    # count under the cap and both insert past it (same as waivers).
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('notification_destinations'), :t)"), {"t": ctx.org.tenant_id})
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


def _get_or_404(
    db: Session, ctx: OrgContext, destination_id: int, *, for_update: bool = False
) -> NotificationDestination:
    dest = notification_repo.get(db, ctx.org.tenant_id, destination_id, for_update=for_update)
    if dest is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    return dest


@router.patch("/orgs/{org_login}/notification-destinations/{destination_id}", response_model=DestinationOut)
def update_destination(
    destination_id: RowId,
    body: DestinationUpdate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if body.events is not None and (body.add_events or body.remove_events):
        raise HTTPException(status_code=422, detail="Send either events or add_events/remove_events, not both")
    # Validated before the row lock: it resolves DNS, which must not run while holding the lock.
    if body.url is not None:
        try:
            notifications.validate_destination_url(body.url.get_secret_value())
        except notifications.UnsafeDestinationURL as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    # Locked so a delta is applied to the stored set, not to a concurrent admin's stale copy.
    dest = _get_or_404(db, ctx, destination_id, for_update=True)
    sent = body.model_fields_set
    key = settings.job_secret_key.get_secret_value()
    changed: list[str] = []
    target_changed = False  # a new url/secret/recipients value, not a resubmission of the stored one

    if dest.kind == "email":
        if body.url is not None or body.secret is not None:
            raise HTTPException(status_code=422, detail="An email destination takes recipients, not a url or secret")
        if body.recipients is not None:
            new_recipients = _email_recipients_or_422(db, ctx, body.recipients)
            target_changed |= new_recipients != decrypt_job_token(dest.encrypted_url, key)
            dest.encrypted_url = encrypt_job_token(new_recipients, key)
            changed.append("recipients")
    else:
        if body.recipients is not None:
            raise HTTPException(status_code=422, detail="Only an email destination takes recipients")
        if body.secret is not None and dest.kind != "generic":
            raise HTTPException(status_code=422, detail="Only a generic webhook destination takes a signing secret")
        if body.secret is not None and not body.secret.get_secret_value():
            raise HTTPException(status_code=422, detail="A signing secret cannot be empty")
        if body.remove_secret:
            if dest.kind != "generic":
                raise HTTPException(status_code=422, detail="Only a generic webhook destination takes a signing secret")
            if body.secret is not None:
                raise HTTPException(status_code=422, detail="Send either secret or remove_secret, not both")
        if body.url is not None:
            new_url = body.url.get_secret_value()
            target_changed |= new_url != decrypt_job_token(dest.encrypted_url, key)
            dest.encrypted_url = encrypt_job_token(new_url, key)
            changed.append("url")
        if body.secret is not None:
            new_secret = body.secret.get_secret_value()
            old_secret = decrypt_job_token(dest.encrypted_secret, key) if dest.encrypted_secret else None
            target_changed |= new_secret != old_secret
            dest.encrypted_secret = encrypt_job_token(new_secret, key)
            changed.append("secret")
        elif body.remove_secret and dest.encrypted_secret is not None:
            dest.encrypted_secret = None
            target_changed = True
            changed.append("secret_removed")

    # `null` on these is "not provided", not "clear": they are NOT NULL columns.
    re_enabled = "enabled" in sent and body.enabled is True and not dest.enabled
    for field in ("name", "enabled", "min_score_drop"):
        if field in sent and getattr(body, field) is not None:
            setattr(dest, field, getattr(body, field))
            changed.append(field)
    if "events" in sent and body.events is not None:
        dest.events = list(body.events)
        changed.append("events")
    elif body.add_events or body.remove_events:
        removed = set(body.remove_events or [])
        current = [e for e in dest.events if e not in removed]
        dest.events = current + [e for e in dict.fromkeys(body.add_events or []) if e not in current]
        changed.append("events")

    # Delivery health describes the old target: after a fix (new URL/secret/recipients) or a re-enable it
    # would keep showing "failing" until some later send, which a rarely-triggered event may never make.
    if re_enabled or target_changed:
        dest.last_status = None
        dest.last_error = None
        dest.consecutive_failures = 0

    audit_repo.write(
        db,
        actor=user.email,
        action="notification.destination_updated",
        target=ctx.org.github_login,
        # Names of what changed only: a URL or secret never goes into the audit log.
        payload={"destination_id": dest.id, "kind": dest.kind, "changed": changed},
        tenant_id=ctx.org.tenant_id,
        commit=False,
    )
    db.commit()
    db.refresh(dest)
    return _out(dest)


@router.delete("/orgs/{org_login}/notification-destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_destination(
    destination_id: RowId,
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


@router.post(
    "/orgs/{org_login}/notification-destinations/announce-open-alerts", response_model=AnnounceOpenAlertsResult
)
def announce_open_alerts(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Send the currently open critical/high alerts once, to destinations subscribed to new critical alerts.

    The security-alert sweep stamps `alert_notified_at` whether or not a destination existed, so alerts that were
    already open when the first destination was added are never announced. This sends that backlog on request
    and deliberately leaves every stamp alone: it is a one-off message, not the sweep's "announced once" ledger."""
    check_account_rate_limit(f"notification-announce:{ctx.org.tenant_id}", max_requests=_ANNOUNCES_PER_MINUTE)
    rows = db.execute(
        select(SecurityAlert.repo, SecurityAlert.kind, SecurityAlert.number, SecurityAlert.severity)
        .where(
            SecurityAlert.tenant_id == ctx.org.tenant_id,
            SecurityAlert.state == "open",
            (SecurityAlert.kind == "secret_scanning") | SecurityAlert.severity.in_(("critical", "high")),
        )
        .order_by(SecurityAlert.ingested_at, SecurityAlert.id)
        .limit(_MAX_ANNOUNCED_ALERTS + 1)
    ).all()
    truncated = len(rows) > _MAX_ANNOUNCED_ALERTS
    alerts = [
        {"repo": r.repo, "kind": r.kind, "number": r.number, "severity": r.severity}
        for r in rows[:_MAX_ANNOUNCED_ALERTS]
    ]
    notifications.notify_new_critical_alerts(db, ctx.org.tenant_id, ctx.org.github_login, alerts, actor=user.email)
    audit_repo.write(
        db,
        actor=user.email,
        action="notification.open_alerts_announced",
        target=ctx.org.github_login,
        payload={"count": len(alerts), "truncated": truncated},
        tenant_id=ctx.org.tenant_id,
    )
    return AnnounceOpenAlertsResult(announced=len(alerts), truncated=truncated)


@router.post("/orgs/{org_login}/notification-destinations/{destination_id}/test", response_model=TestSendResult)
def test_destination(
    destination_id: RowId,
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
    notification_repo.record_delivery(db, dest.id, ok, detail)
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
