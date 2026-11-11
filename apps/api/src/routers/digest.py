"""An org's own leadership-digest controls: follow or override the instance cadence, choose recipients,
preview the content, and send a test to yourself. The scheduled send is services/digest_sweep.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rate_limit import rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo
from src.schemas.digest import DigestPreview, DigestSettings, DigestSettingsUpdate, DigestTestResult
from src.services import digest_service, digest_sweep, email, notifications

logger = logging.getLogger(__name__)

router = APIRouter()


def _instance_cadence() -> str:
    return digest_sweep._read_cadence() or "off"


def _settings(db: Session, org) -> DigestSettings:
    instance = _instance_cadence()
    cadence = digest_sweep.effective_cadence(org.digest_enabled, None if instance == "off" else instance)
    return DigestSettings(
        enabled=org.digest_enabled,
        effective=cadence is not None,
        cadence=cadence,
        instance_cadence=instance,
        recipients=org.digest_recipients,
        resolved_recipients=sorted(digest_sweep.recipients_for(db, org.tenant_id, org.digest_recipients)),
        available_recipients=sorted(notifications.verified_member_emails(db, org.tenant_id)),
        smtp_configured=email.is_configured(),
    )


@router.get("/orgs/{org_login}/digest", response_model=DigestSettings)
def get_digest_settings(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")), db: Session = Depends(get_db)
):
    return _settings(db, ctx.org)


@router.put("/orgs/{org_login}/digest", response_model=DigestSettings)
def set_digest_settings(
    body: DigestSettingsUpdate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    org = ctx.org
    changes: dict = {}
    if "enabled" in body.model_fields_set and org.digest_enabled != body.enabled:
        changes["enabled"] = {"from": org.digest_enabled, "to": body.enabled}
        org.digest_enabled = body.enabled
    if "recipients" in body.model_fields_set:
        chosen = None if body.recipients is None else sorted({str(r).lower() for r in body.recipients})
        if chosen is not None:
            # Recipients must be people who can already see this org's data, like email alert destinations.
            allowed = notifications.verified_member_emails(db, org.tenant_id)
            outside = [r for r in chosen if r not in allowed]
            if outside:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Recipients must be verified members of this organization: " + ", ".join(outside),
                )
        if org.digest_recipients != chosen:
            changes["recipients"] = {"from": org.digest_recipients, "to": chosen}
            org.digest_recipients = chosen
    if changes:
        audit_repo.write(
            db, actor=user.email, action="digest.settings_updated", target=org.github_login,
            payload=changes, tenant_id=org.tenant_id, commit=False,
        )
        db.commit()
    return _settings(db, org)


def _build(db: Session, org) -> digest_service.DigestContent:
    settings = _settings(db, org)
    label = settings.cadence or ("weekly" if settings.instance_cadence == "off" else settings.instance_cadence)
    return digest_service.build_digest(db, tenant_id=org.tenant_id, org_login=org.github_login, period_label=label)


@router.get("/orgs/{org_login}/digest/preview", response_model=DigestPreview)
def preview_digest(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")), db: Session = Depends(get_db)
):
    """What the digest would say right now, built from stored data; nothing is sent."""
    content = _build(db, ctx.org)
    return DigestPreview(
        subject=digest_service.render_subject(content), body=digest_service.render_text(content), empty=content.is_empty
    )


@router.post("/orgs/{org_login}/digest/test", response_model=DigestTestResult, dependencies=[Depends(rate_limit())])
def send_test_digest(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Email the current digest to the caller only. Needs SMTP and a verified address."""
    if not email.is_configured():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email (SMTP) is not configured on this server")
    if user.email.lower() not in notifications.verified_member_emails(db, ctx.org.tenant_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Verify your email address to receive a test digest")
    content = _build(db, ctx.org)
    try:
        email.send_email(user.email, "[Test] " + digest_service.render_subject(content), digest_service.render_text(content))
    except Exception as exc:
        logger.exception("test digest to %s failed", user.email)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The test email could not be sent") from exc
    audit_repo.write(
        db, actor=user.email, action="digest.test_sent", target=ctx.org.github_login,
        payload={"to": user.email}, tenant_id=ctx.org.tenant_id,
    )
    return DigestTestResult(sent_to=user.email)
