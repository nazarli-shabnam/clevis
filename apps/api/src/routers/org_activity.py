"""An org's own audit trail and background jobs, for the org's admins.

``/audit`` and ``/jobs`` are workspace-admin only, which leaves an org admin who isn't the
instance's workspace admin unable to see who did what in their org or whether their jobs ran. These
read the same data, scoped to the org's tenant, behind ``require_org_role("admin")``.

* Audit: only rows written under the org's tenant (instance-wide ``config.update`` rows are
  attributed to the acting admin's personal tenant, and rows from before attribution have no
  tenant, so neither appears here).
* Jobs: only jobs enqueued for the org's tenant. The response carries the ``JobOut`` fields and
  never the payload, which holds an encrypted GitHub token.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, job_repo
from src.schemas.audit import AuditLogOut
from src.schemas.job import JobOut

router = APIRouter()

MAX_LIMIT = 500


@router.get("/orgs/{org_login}/audit", response_model=list[AuditLogOut])
def org_audit_log(
    org_login: str,
    action_prefix: str | None = Query(default=None, max_length=100, description="Actions starting with this, e.g. 'token.'"),
    actor: str | None = Query(default=None, max_length=320, description="Exact actor (case-insensitive)"),
    target: str | None = Query(default=None, max_length=200, description="Target containing this text"),
    since: datetime | None = Query(default=None, description="Created at or after this time"),
    until: datetime | None = Query(default=None, description="Created before this time"),
    before_id: int | None = Query(default=None, ge=1, description="Cursor: only rows older than this id"),
    limit: int = Query(default=100, ge=1, le=MAX_LIMIT),
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    return audit_repo.list_for_tenant(
        db,
        ctx.org.tenant_id,
        action_prefix=action_prefix,
        actor=actor,
        target=target,
        since=since,
        until=until,
        before_id=before_id,
        limit=limit,
    )


@router.get("/orgs/{org_login}/jobs", response_model=list[JobOut])
def org_jobs(
    org_login: str,
    before_id: int | None = Query(default=None, ge=1, description="Cursor: only jobs older than this id"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    return job_repo.list_for_tenant(db, ctx.org.tenant_id, limit=limit, before_id=before_id)
