"""Per-tick work for the waiver-expiry loop.

A lapsed waiver needs no action to stop applying (scoring only counts unexpired ones), so this exists
only to *announce*: the lapse once (audits ``waiver.expired`` and messages alert destinations subscribed to
``waiver_expired``; ``expiry_noted_at`` makes that once per waiver) and, ahead of it, the "about to expire"
warnings (audits ``waiver.expiring`` and messages ``waiver_expiring`` destinations; ``expiry_warning_stage``
makes each warning window once per expiry). The in-app notification center derives its own items from
``expires_at`` and needs nothing from here.
Shape follows digest_sweep.py (org tenants, per-tenant advisory slot so replicas don't double-announce).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import set_session_tenant
from src.repositories import audit_repo, waiver_repo
from src.services import notifications, waivers as waiver_service
from src.services.sweep_lock import try_acquire_sweep_slot

logger = logging.getLogger(__name__)

_SWEEP_KEY = "waiver_expiry"
ACTOR = "system:waiver_expiry"


def _due_warnings(db: Session, tenant_id: int, now: datetime) -> list[tuple]:
    """(waiver, stage) for each unexpired waiver that crossed a warning window nobody was told about yet.
    A waiver that crossed two windows between ticks (a long outage) is announced once, at the later stage."""
    due = []
    # Locked until this tick's commit: a renewal that lands meanwhile waits, instead of being overwritten by a
    # stage computed from the expiry it replaced.
    for waiver in waiver_repo.candidates_for_expiry_warning(
        db, tenant_id, now, max(waiver_service.WARNING_DAYS), lock=True
    ):
        stage = waiver_service.warning_stage(waiver.expires_at, now)
        if stage > waiver.expiry_warning_stage:
            due.append((waiver, stage))
    return due


def run_waiver_expiry_sweep(db: Session) -> None:
    now = datetime.now(timezone.utc)
    # tenants has no RLS (identity table) -- read freely, then scope per tenant below.
    tenants = db.execute(
        text("SELECT t.id, o.github_login FROM tenants t JOIN orgs o ON o.id = t.org_id WHERE t.kind = 'org'")
    ).fetchall()

    for tenant_id, org_login in tenants:
        try:
            set_session_tenant(db, tenant_id)
            if not try_acquire_sweep_slot(db, _SWEEP_KEY, tenant_id):
                continue
            due = waiver_repo.due_for_expiry_notice(db, tenant_id, now)
            warnings = _due_warnings(db, tenant_id, now)
            if not due and not warnings:
                db.commit()  # release the advisory slot
                continue
            announced = []
            for waiver in due:
                waiver.expiry_noted_at = now
                payload = {
                    "waiver_id": waiver.id,
                    "check_id": waiver.check_id,
                    "repos": waiver.repos,
                    "owner": waiver.owner,
                    "reason": waiver.reason,
                }
                audit_repo.write(
                    db, actor=ACTOR, action="waiver.expired", target=org_login,
                    payload=payload, tenant_id=tenant_id, commit=False,
                )
                announced.append(payload)
            expiring = []
            for waiver, stage in warnings:
                waiver.expiry_warning_stage = stage
                payload = {
                    "waiver_id": waiver.id,
                    "check_id": waiver.check_id,
                    "repos": waiver.repos,
                    "owner": waiver.owner,
                    "expires_at": waiver.expires_at.isoformat(),
                    "days_left": waiver_service.WARNING_DAYS[stage - 1],
                }
                audit_repo.write(
                    db, actor=ACTOR, action="waiver.expiring", target=org_login,
                    payload=payload, tenant_id=tenant_id, commit=False,
                )
                expiring.append(payload)
            db.commit()  # markers and audit rows land together; the alerts below are best-effort
            for send, items in (
                (notifications.notify_waiver_expired, announced),
                (notifications.notify_waiver_expiring, expiring),
            ):
                if not items:
                    continue
                try:
                    send(db, tenant_id, org_login, items, actor=ACTOR)
                except Exception:
                    db.rollback()
                    logger.exception("%s failed for tenant %d", send.__name__, tenant_id)
        except Exception:
            db.rollback()
            logger.exception("waiver expiry sweep: iteration failed for tenant %d", tenant_id)
