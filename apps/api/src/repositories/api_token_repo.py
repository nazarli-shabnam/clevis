"""Helpers for ``api_tokens``. Management calls run under an org admin's tenant context (RLS);
token *resolution* happens before any tenant is known, so it goes through the narrow
``resolve_api_token`` SECURITY DEFINER function (migration 0049)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from src.core.db import ApiToken
from src.repositories import audit_repo

TOKEN_PREFIX = "clv_"


def generate() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # 256-bit random tokens: a fast hash is enough (no password-style stretching needed).
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class ResolvedToken:
    token_id: int
    tenant_id: int
    org_id: int
    org_login: str
    scope: str


def resolve(db: Session, token: str) -> ResolvedToken | None:
    row = db.execute(
        text("SELECT token_id, tenant_id, org_id, org_login, scope FROM resolve_api_token(:h)"),
        {"h": hash_token(token)},
    ).first()
    return ResolvedToken(*row) if row else None


# last_used_at is informational ("is this token still in use?"), so minute precision is plenty. Writing
# it on every request would rewrite one hot row per CI call.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def touch(db: Session, token_id: int) -> None:
    """Stamp last_used_at at most once per LAST_USED_RESOLUTION; a no-op (no row write, no commit) otherwise."""
    result = db.execute(
        update(ApiToken)
        .where(
            ApiToken.id == token_id,
            or_(ApiToken.last_used_at.is_(None), ApiToken.last_used_at < func.now() - LAST_USED_RESOLUTION),
        )
        .values(last_used_at=func.now())
    )
    if result.rowcount:
        db.commit()


def create(
    db: Session,
    *,
    tenant_id: int,
    name: str,
    created_by: str,
    created_by_user_id: int | None = None,
    expires_in_days: int | None = None,
) -> tuple[ApiToken, str]:
    token = generate()
    row = ApiToken(
        tenant_id=tenant_id,
        name=name,
        token_hash=hash_token(token),
        prefix=token[: len(TOKEN_PREFIX) + 4],
        created_by=created_by,
        created_by_user_id=created_by_user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(row)
    db.flush()
    return row, token


def list_for_tenant(db: Session, tenant_id: int) -> list[ApiToken]:
    return list(db.execute(select(ApiToken).where(ApiToken.tenant_id == tenant_id).order_by(ApiToken.id)).scalars())


def get(db: Session, tenant_id: int, token_id: int) -> ApiToken | None:
    return db.execute(
        select(ApiToken).where(ApiToken.tenant_id == tenant_id, ApiToken.id == token_id)
    ).scalar_one_or_none()


def revoke_for_departed_creator(db: Session, tenant_id: int, user_id: int) -> int:
    """Revoke the active tokens ``user_id`` created in this org, if the org opted in
    (``orgs.revoke_tokens_on_creator_leave``). Called wherever a membership is removed; a no-op otherwise.
    The worker's ``revoke_github_membership`` does the same in raw SQL. Audited per token; does not commit."""
    revoked = db.execute(
        text(
            """
            UPDATE api_tokens t SET revoked_at = now()
            WHERE t.tenant_id = :t AND t.created_by_user_id = :u AND t.revoked_at IS NULL
              AND EXISTS (SELECT 1 FROM orgs o WHERE o.tenant_id = t.tenant_id AND o.revoke_tokens_on_creator_leave)
            RETURNING t.id, t.name
            """
        ),
        {"t": tenant_id, "u": user_id},
    ).all()
    for token_id, name in revoked:
        audit_repo.write(
            db, "system", "api_token.auto_revoked", name,
            {"token_id": token_id, "reason": "creator_left_org", "creator_user_id": user_id},
            tenant_id=tenant_id, commit=False,
        )
    return len(revoked)


def revoke_for_departed_creators(db: Session, tenant_id: int) -> int:
    """Revoke every active token in this org whose creator is no longer a member: the one-time catch-up when
    ``orgs.revoke_tokens_on_creator_leave`` is switched on, because the per-departure revoke above only sees
    removals that happen while it is on. A token with an unknown creator (``created_by_user_id`` NULL) is not
    treated as departed. Audited per token like the per-departure revoke; does not commit."""
    revoked = db.execute(
        text(
            """
            UPDATE api_tokens t SET revoked_at = now()
            WHERE t.tenant_id = :t AND t.revoked_at IS NULL AND t.created_by_user_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM memberships m WHERE m.tenant_id = t.tenant_id AND m.user_id = t.created_by_user_id
              )
            RETURNING t.id, t.name, t.created_by_user_id
            """
        ),
        {"t": tenant_id},
    ).all()
    for token_id, name, creator_id in revoked:
        audit_repo.write(
            db, "system", "api_token.auto_revoked", name,
            {"token_id": token_id, "reason": "creator_left_org", "creator_user_id": creator_id, "when": "policy_enabled"},
            tenant_id=tenant_id, commit=False,
        )
    return len(revoked)
