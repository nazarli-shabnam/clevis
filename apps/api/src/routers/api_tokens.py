"""API tokens for CI, the machine score endpoints they unlock, and the opt-in score badge.

Tokens are org-scoped and cannot change configuration: they can read the latest scan and trigger a
fresh one. A scan only reads GitHub, but it does store a score snapshot (feeding the badge, the score
API and trend history) and can send score-drop alerts, so "read" is a scope name, not a promise that
nothing is written. Scans are single-flight per org and reused for a short window (see ``run_scan``).
"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.api_token_auth import require_scope
from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rate_limit import check_account_rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import api_token_repo, audit_repo, org_repo, scan_results_repo
from src.repositories.api_token_repo import ResolvedToken
from src.routers.analytics import _run_overview, persist_scan_and_alert
from src.schemas.api_token import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenOut,
    BadgeSettings,
    ScoreCheck,
    ScoreOut,
)
from src.services.analytics_service import org_scores_hygiene
from src.services.token_resolution import NoGitHubTokenAvailable, resolve_org_token

router = APIRouter()
logger = logging.getLogger(__name__)

_MAX_TOKENS_PER_ORG = 20

# A scan costs hundreds of GitHub calls under the org's shared installation token. One that finished
# within this window is returned instead of running another, so a looping or parallel CI job can't
# burn the org's rate limit.
_SCAN_REUSE_SECONDS = 60
# ponytail: per-process lock, so N API replicas allow N concurrent scans per org; a Redis/advisory
# lock if replicas ever matter. One entry per org that ever scanned, so it can't grow unboundedly.
_scan_locks: dict[str, asyncio.Lock] = {}


def _recent_scan(db: Session, token: ResolvedToken) -> dict | None:
    scan = scan_results_repo.latest_with_checks(db, token.org_login, token.tenant_id)
    if scan is None or not scan["scanned_at"]:
        return None
    age = datetime.now(timezone.utc) - datetime.fromisoformat(scan["scanned_at"])
    return scan if age.total_seconds() < _SCAN_REUSE_SECONDS else None


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
    now = datetime.now(timezone.utc)
    active = [
        t
        for t in api_token_repo.list_for_tenant(db, ctx.org.tenant_id)
        if t.revoked_at is None and (t.expires_at is None or t.expires_at > now)
    ]
    if len(active) >= _MAX_TOKENS_PER_ORG:
        raise HTTPException(status_code=409, detail=f"At most {_MAX_TOKENS_PER_ORG} active tokens per organization")
    row, token = api_token_repo.create(
        db,
        tenant_id=ctx.org.tenant_id,
        name=body.name,
        created_by=user.email,
        expires_in_days=body.expires_in_days,
    )
    audit_repo.write(
        db,
        actor=user.email,
        action="api_token.created",
        target=ctx.org.github_login,
        payload={
            "token_id": row.id,
            "name": body.name,
            "prefix": row.prefix,
            "scope": row.scope,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
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
def latest_score(org_login: str, token: ResolvedToken = Depends(require_scope("read")), db: Session = Depends(get_db)):
    _own_org(org_login, token)
    scan = scan_results_repo.latest_with_checks(db, token.org_login, token.tenant_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="No scan yet; run one with POST /api/v1/orgs/{org}/scan")
    return _score_out(token.org_login, scan)


@router.post("/api/v1/orgs/{org_login}/scan", response_model=ScoreOut)
async def run_scan(org_login: str, token: ResolvedToken = Depends(require_scope("read")), db: Session = Depends(get_db)):
    _own_org(org_login, token)
    # Callers queue behind an in-flight scan, then find its result as the "recent" one and return it.
    async with _scan_locks.setdefault(token.org_login.lower(), asyncio.Lock()):
        recent = await anyio.to_thread.run_sync(lambda: _recent_scan(db, token))
        if recent is not None:
            return _score_out(token.org_login, recent)
        return await _scan_and_record(token, db)


async def _scan_and_record(token: ResolvedToken, db: Session) -> ScoreOut:
    try:
        github_token = await anyio.to_thread.run_sync(
            lambda: resolve_org_token(db, org_id=token.org_id, account_login=token.org_login, client_token=None)
        )
    except NoGitHubTokenAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    org = await anyio.to_thread.run_sync(lambda: org_repo.get_by_id(db, token.org_id))
    result = await _run_overview(token.org_login, github_token, score_hygiene=org_scores_hygiene(org))

    # Attributes the scan, and any alert it triggers, to this token rather than to "system".
    actor = f"api_token:{token.token_id}"

    # One thread for the whole DB sequence: a Session is not safe to share between threads at once,
    # and these steps depend on each other anyway.
    def _record_and_notify() -> dict:
        previous = persist_scan_and_alert(
            db, org_repo.get_by_id(db, token.org_id), result, token.tenant_id, actor=actor
        )
        # The scan is already stored (and any alert sent); a failing audit write must not 500 it, or a CI
        # retry would just scan twice.
        try:
            audit_repo.write(
                db,
                actor=actor,
                action="api_token.scan",
                target=token.org_login,
                payload={
                    "token_id": token.token_id,
                    "score": result["score"],
                    "previous_score": previous["score"] if previous else None,
                },
                tenant_id=token.tenant_id,
            )
        except Exception:
            db.rollback()
            logger.exception("could not audit the api-token scan for %s", token.org_login)
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
    # Drop this process's cached answer so opting out takes effect at once here (other replicas
    # keep serving theirs for up to _BADGE_CACHE_TTL_SECONDS).
    _badge_invalidate(ctx.org.github_login)
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


# The badge is unauthenticated, so reads are cached per login (including "no badge") and only cache
# misses, which hit the database, are rate limited. Per-process: with several API replicas each keeps
# its own cache and budget. Bounded so a stream of random logins can't grow it without limit.
_BADGE_CACHE_TTL_SECONDS = 60
_BADGE_CACHE_MAX_ENTRIES = 1024
_BADGE_MISS_LIMIT_PER_MINUTE = 60
_badge_cache: dict[str, tuple[float, int | None]] = {}
# Guards the cache and `_badge_epoch`. The epoch is bumped by every invalidation; a lookup records it
# before reading the database and only stores its answer if it is unchanged afterwards, so a read that
# started before an opt-out cannot write the pre-opt-out score back after the entry was dropped. One
# counter for all logins is deliberate: a change elsewhere only makes an in-flight lookup skip caching.
_badge_lock = threading.Lock()
_badge_epoch = 0


def _badge_invalidate(org_login: str) -> None:
    global _badge_epoch
    with _badge_lock:
        _badge_epoch += 1
        _badge_cache.pop(org_login.lower(), None)


def _badge_score(db: Session, request: Request, org_login: str) -> int | None:
    key = org_login.lower()
    with _badge_lock:
        hit = _badge_cache.get(key)
        epoch = _badge_epoch
    if hit is not None and time.monotonic() - hit[0] < _BADGE_CACHE_TTL_SECONDS:
        return hit[1]
    # Keyed by client IP alone (not path): a per-path key would let a caller dodge the limit by
    # cycling through org names.
    ip = request.client.host if request.client else "unknown"
    check_account_rate_limit(f"badge:{ip}", max_requests=_BADGE_MISS_LIMIT_PER_MINUTE)
    score = db.execute(text("SELECT public_badge_score(:login)"), {"login": org_login}).scalar()
    with _badge_lock:
        if epoch == _badge_epoch:
            if len(_badge_cache) >= _BADGE_CACHE_MAX_ENTRIES:
                _badge_cache.clear()
            _badge_cache[key] = (time.monotonic(), score)
    return score


@router.get("/badges/{org_login}/score.svg")
def score_badge(org_login: str, request: Request, db: Session = Depends(get_db)):
    # Disabled, unknown and never-scanned orgs all look identical (404), so the badge can't be used
    # to discover which orgs exist or have opted out. Only the bare score ever leaves this endpoint.
    score = _badge_score(db, request, org_login)
    if score is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=_badge_svg(int(score)),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )
