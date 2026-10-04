"""Helpers for ``api_tokens``. Management calls run under an org admin's tenant context (RLS);
token *resolution* happens before any tenant is known, so it goes through the narrow
``resolve_api_token`` SECURITY DEFINER function (migration 0049)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from src.core.db import ApiToken

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


def touch(db: Session, token_id: int) -> None:
    db.execute(update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=text("now()")))
    db.commit()


def create(db: Session, *, tenant_id: int, name: str, created_by: str) -> tuple[ApiToken, str]:
    token = generate()
    row = ApiToken(
        tenant_id=tenant_id,
        name=name,
        token_hash=hash_token(token),
        prefix=token[: len(TOKEN_PREFIX) + 4],
        created_by=created_by,
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
