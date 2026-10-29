"""Compliance evidence export for an org: one zip an admin can hand to an auditor. Org admins only.

The export is itself audited (``evidence.export``), rate-limited, and never cached."""

import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rate_limit import check_account_rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo
from src.services import evidence_bundle

router = APIRouter()

_EXPORTS_PER_WINDOW = 5


def _window(since: date | None, until: date | None) -> tuple[datetime | None, datetime | None]:
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=422, detail="`since` must not be after `until`")
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc) if since else None
    # `until` is an inclusive calendar day: widen to the end of it.
    until_dt = datetime.combine(until, datetime.max.time(), tzinfo=timezone.utc) if until else None
    return since_dt, until_dt


@router.get("/orgs/{org_login}/evidence")
def export_evidence(
    since: date | None = None,
    until: date | None = None,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    since_dt, until_dt = _window(since, until)
    # Building the bundle reads a lot of rows; bound how often one org can ask.
    check_account_rate_limit(f"evidence:{ctx.org.tenant_id}", max_requests=_EXPORTS_PER_WINDOW)

    body, manifest = evidence_bundle.build_bundle(
        db,
        org_login=ctx.org.github_login,
        tenant_id=ctx.org.tenant_id,
        generated_by=user.email,
        since=since_dt,
        until=until_dt,
    )
    audit_repo.write(
        db,
        actor=user.email,
        action="evidence.export",
        target=ctx.org.github_login,
        payload={
            "range": manifest["range"],
            "truncated": manifest["truncated"],
            "rows": {name: f["rows"] for name, f in manifest["files"].items()},
        },
        tenant_id=ctx.org.tenant_id,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_org = re.sub(r"[^A-Za-z0-9_.-]", "_", ctx.org.github_login)
    return Response(
        content=body,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="clevis-evidence-{safe_org}-{stamp}.zip"',
            # An evidence bundle holds the audit log and roster: never keep it in a shared or browser cache.
            "Cache-Control": "no-store",
            "X-Evidence-Truncated": "true" if manifest["truncated"] else "false",
        },
    )
