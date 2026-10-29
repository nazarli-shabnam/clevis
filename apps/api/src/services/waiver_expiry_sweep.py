"""Per-tick work for the waiver-expiry loop.

A lapsed waiver needs no action to stop applying (scoring only counts unexpired ones), so this exists
only to *announce* the lapse once: it audits ``waiver.expired`` and messages alert destinations
subscribed to ``waiver_expired``. ``expiry_noted_at`` makes that happen once per waiver; the in-app
notification center derives its own item from ``expires_at`` and needs nothing from here.
Shape follows digest_sweep.py (org tenants, per-tenant advisory slot so replicas don't double-announce).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import set_session_tenant
from src.repositories import audit_repo, waiver_repo
from src.services import notifications
from src.services.sweep_lock import try_acquire_sweep_slot

logger = logging.getLogger(__name__)

_SWEEP_KEY = "waiver_expiry"
ACTOR = "system:waiver_expiry"


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
            if not due:
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
            db.commit()  # marker and audit rows land together; the alert below is best-effort
            try:
                notifications.notify_waiver_expired(db, tenant_id, org_login, announced, actor=ACTOR)
            except Exception:
                db.rollback()
                logger.exception("waiver expiry alert failed for tenant %d", tenant_id)
        except Exception:
            db.rollback()
            logger.exception("waiver expiry sweep: iteration failed for tenant %d", tenant_id)
