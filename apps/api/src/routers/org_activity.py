"""An org's own audit trail and background jobs, for the org's admins.

``/audit`` and ``/jobs`` are workspace-admin only, which leaves an org admin who isn't the
instance's workspace admin unable to see who did what in their org or whether their jobs ran. These
read the same data, scoped to the org's tenant, behind ``require_org_role("admin")``.

* Audit: only rows written under the org's tenant (instance-wide ``config.update`` rows are
  attributed to the acting admin's personal tenant, and rows from before attribution have no
  tenant, so neither appears here).
* Audit export: the same rows and filters as a CSV, all matching rows up to a cap rather than the pages a
  browser has loaded; the export is itself recorded in the audit log.
* Jobs: only jobs enqueued for the org's tenant. The response carries the ``JobOut`` fields and
  never the payload, which holds an encrypted GitHub token.
"""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rate_limit import check_account_rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, job_repo
from src.schemas.audit import AuditLogOut
from src.schemas.job import JobOut
from src.services.evidence_bundle import to_csv

router = APIRouter()

MAX_LIMIT = 500
# One export holds every matching row in memory, so it is capped; past the cap the response says so
# (``X-Audit-Truncated``) instead of being silently partial. Same order of magnitude as the evidence bundle's cap.
AUDIT_EXPORT_LIMIT = 50_000
_EXPORTS_PER_WINDOW = 5


def _utc(value: datetime | None) -> datetime | None:
    """A timestamp without an offset is read as UTC, not in the DB session's timezone."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _checked_range(since: datetime | None, until: datetime | None) -> tuple[datetime | None, datetime | None]:
    since, until = _utc(since), _utc(until)
    if since is not None and until is not None and since >= until:
        raise HTTPException(status_code=422, detail="'since' must be earlier than 'until'")
    return since, until


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
    since, until = _checked_range(since, until)
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


@router.get("/orgs/{org_login}/audit/export")
def export_org_audit_log(
    org_login: str,
    action_prefix: str | None = Query(default=None, max_length=100),
    actor: str | None = Query(default=None, max_length=320),
    target: str | None = Query(default=None, max_length=200),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Every audit row matching the filters (newest first, up to ``AUDIT_EXPORT_LIMIT``) as CSV, with each
    row's payload. Takes the same filters as the list above."""
    since, until = _checked_range(since, until)
    # A large export reads and renders many rows; bound how often one org can ask (as the evidence bundle does).
    check_account_rate_limit(f"audit-export:{ctx.org.tenant_id}", max_requests=_EXPORTS_PER_WINDOW)
    # One extra row tells a full export from a capped one.
    rows = audit_repo.list_for_tenant(
        db,
        ctx.org.tenant_id,
        action_prefix=action_prefix,
        actor=actor,
        target=target,
        since=since,
        until=until,
        limit=AUDIT_EXPORT_LIMIT + 1,
    )
    truncated = len(rows) > AUDIT_EXPORT_LIMIT
    rows = rows[:AUDIT_EXPORT_LIMIT]
    body = to_csv(
        ["id", "created_at", "actor", "action", "target", "payload"],
        [[r.id, r.created_at.isoformat() if r.created_at else "", r.actor, r.action, r.target, r.payload] for r in rows],
    )
    # Written after the read, so the export does not list itself.
    audit_repo.write(
        db,
        actor=user.email,
        action="audit.export",
        target=ctx.org.github_login,
        payload={
            "filters": {
                k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in {"action_prefix": action_prefix, "actor": actor, "target": target, "since": since, "until": until}.items()
                if v
            },
            "rows": len(rows),
            "truncated": truncated,
        },
        tenant_id=ctx.org.tenant_id,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", ctx.org.github_login)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="clevis-audit-{safe_org}-{stamp}.csv"',
            # The audit trail: never keep it in a shared or browser cache.
            "Cache-Control": "no-store",
            "X-Audit-Truncated": "true" if truncated else "false",
        },
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
