"""API tokens for CI, the machine score endpoints they unlock, and the opt-in score badge.

Tokens are org-scoped and read-only: they can read the latest scan and trigger a fresh one (which
only reads GitHub and stores a snapshot), nothing that changes configuration.
"""

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.api_token_auth import require_api_token
from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.repositories import api_token_repo, audit_repo, org_repo, scan_results_repo
from src.repositories.api_token_repo import ResolvedToken
from src.routers.analytics import _notify_score_drop_best_effort, _persist_scan, _run_overview
from src.schemas.api_token import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenOut,
    BadgeSettings,
    ScoreCheck,
    ScoreOut,
)
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()

_MAX_TOKENS_PER_ORG = 20


# --- token management (org admins, session auth) ---

@router.get("/orgs/{org_login}/api-tokens", response_model=list[ApiTokenOut])
def list_tokens(ctx: OrgContext = Depends(require_org_role(min_role="admin")), db: Session = Depends(get_db)):
    return api_token_repo.list_for_tenant(db, ctx.org.tenant_id)


@router.post("/orgs/{org_login}/api-tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
def create_token(
    body: ApiTokenCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    active = [t for t in api_token_repo.list_for_tenant(db, ctx.org.tenant_id) if t.revoked_at is None]
    if len(active) >= _MAX_TOKENS_PER_ORG:
        raise HTTPException(status_code=409, detail=f"At most {_MAX_TOKENS_PER_ORG} active tokens per organization")
    row, token = api_token_repo.create(db, tenant_id=ctx.org.tenant_id, name=body.name, created_by=user.email)
    audit_repo.write(
        db,
        actor=user.email,
        action="api_token.created",
        target=ctx.org.github_login,
        payload={"token_id": row.id, "name": body.name, "prefix": row.prefix, "scope": row.scope},
        tenant_id=ctx.org.tenant_id,
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return ApiTokenCreated(**ApiTokenOut.model_validate(row).model_dump(), token=token)


@router.delete("/orgs/{org_login}/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(
    token_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    row = api_token_repo.get(db, ctx.org.tenant_id, token_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if row.revoked_at is None:
        db.execute(text("UPDATE api_tokens SET revoked_at = now() WHERE id = :id"), {"id": row.id})
        audit_repo.write(
            db,
            actor=user.email,
            action="api_token.revoked",
            target=ctx.org.github_login,
            payload={"token_id": row.id, "name": row.name, "prefix": row.prefix},
            tenant_id=ctx.org.tenant_id,
            commit=False,
        )
        db.commit()


# --- machine endpoints (bearer token) ---

def _score_out(org_login: str, scan: dict) -> ScoreOut:
    return ScoreOut(
        org=org_login,
        score=scan["score"],
        total_checks=scan["total_checks"],
        failed_checks=scan["failed_checks"],
        scanned_at=scan["scanned_at"],
        checks=[
            ScoreCheck(id=str(c.get("id", "")), title=str(c.get("title", "")), status=str(c.get("status", "")))
            for c in scan["checks"]
            if isinstance(c, dict)
        ],
    )


def _own_org(org_login: str, token: ResolvedToken) -> None:
    # 404 (not 403) so a token can't be used to probe which other orgs exist.
    if org_login.lower() != token.org_login.lower():
        raise HTTPException(status_code=404, detail="Organization not found")


@router.get("/api/v1/orgs/{org_login}/score", response_model=ScoreOut)
def latest_score(org_login: str, token: ResolvedToken = Depends(require_api_token), db: Session = Depends(get_db)):
    _own_org(org_login, token)
    scan = scan_results_repo.latest_with_checks(db, token.org_login, token.tenant_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="No scan yet; run one with POST /api/v1/orgs/{org}/scan")
    return _score_out(token.org_login, scan)


class _Ctx:
    """Just enough of OrgContext for the shared score-drop notifier."""

    def __init__(self, org):
        self.org = org


@router.post("/api/v1/orgs/{org_login}/scan", response_model=ScoreOut)
async def run_scan(org_login: str, token: ResolvedToken = Depends(require_api_token), db: Session = Depends(get_db)):
    _own_org(org_login, token)
    try:
        github_token = await anyio.to_thread.run_sync(
            lambda: resolve_org_token(db, org_id=token.org_id, account_login=token.org_login, client_token=None)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = await _run_overview(token.org_login, github_token)

    # One thread for the whole DB sequence: a Session is not safe to share between threads at once,
    # and these steps depend on each other anyway.
    def _record_and_notify() -> dict:
        previous = scan_results_repo.list_recent(db, token.org_login, limit=1, tenant_id=token.tenant_id)
        _persist_scan(db, result, tenant_id=token.tenant_id)
        if previous:
            org = org_repo.get_by_id(db, token.org_id)
            _notify_score_drop_best_effort(db, _Ctx(org), previous[0]["score"], result["score"])
        return scan_results_repo.latest_with_checks(db, token.org_login, token.tenant_id)

    scan = await anyio.to_thread.run_sync(_record_and_notify)
    return _score_out(token.org_login, scan)


# --- badge (opt-in, public) ---

@router.get("/orgs/{org_login}/badge", response_model=BadgeSettings)
def get_badge_setting(ctx: OrgContext = Depends(require_org_role(min_role="admin"))):
    return BadgeSettings(enabled=ctx.org.badge_enabled)


@router.put("/orgs/{org_login}/badge", response_model=BadgeSettings)
def set_badge_setting(
    body: BadgeSettings,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    ctx.org.badge_enabled = body.enabled
    audit_repo.write(
        db,
        actor=user.email,
        action="badge.enabled" if body.enabled else "badge.disabled",
        target=ctx.org.github_login,
        payload={},
        tenant_id=ctx.org.tenant_id,
        commit=False,
    )
    db.commit()
    return BadgeSettings(enabled=body.enabled)


_BADGE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="security score: {label}">'
    '<rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/>'
    '<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" text-anchor="middle">'
    '<text x="{lx}" y="14">security</text><text x="{vx}" y="14">{label}</text></g></svg>'
)


def _badge_svg(score: int) -> str:
    label = str(score)
    color = "#4c1" if score >= 90 else "#dfb317" if score >= 70 else "#e05d44"
    lw, vw = 62, 14 + 7 * len(label)
    return _BADGE.format(w=lw + vw, lw=lw, vw=vw, color=color, label=label, lx=lw // 2, vx=lw + vw // 2)


@router.get("/badges/{org_login}/score.svg")
def score_badge(org_login: str, db: Session = Depends(get_db)):
    # Disabled, unknown and never-scanned orgs all look identical (404), so the badge can't be used
    # to discover which orgs exist or have opted out. Only the bare score ever leaves this endpoint.
    score = db.execute(text("SELECT public_badge_score(:login)"), {"login": org_login}).scalar()
    if score is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=_badge_svg(int(score)),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )
