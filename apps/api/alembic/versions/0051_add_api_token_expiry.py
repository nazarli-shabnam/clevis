"""Add api_tokens.expires_at and make resolve_api_token ignore expired tokens.

- ``expires_at`` is a nullable column add: NULL means "never expires", so every existing token keeps
  working exactly as before (no backfill, no data loss).
- ``resolve_api_token`` is replaced in place (same signature, owner and EXECUTE grants are kept by
  ``CREATE OR REPLACE``) with one extra predicate, so an expired token fails resolution the same way
  a revoked one does.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

_RESOLVE = """
    CREATE OR REPLACE FUNCTION resolve_api_token(p_hash text)
    RETURNS TABLE (token_id integer, tenant_id integer, org_id integer, org_login text, scope text)
    LANGUAGE sql
    SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
        SELECT t.id, t.tenant_id, o.id, o.github_login, t.scope
        FROM api_tokens t JOIN orgs o ON o.tenant_id = t.tenant_id
        WHERE t.token_hash = p_hash AND t.revoked_at IS NULL{expiry}
        LIMIT 1
    $$;
"""


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(_RESOLVE.format(expiry=" AND (t.expires_at IS NULL OR t.expires_at > now())"))


def downgrade() -> None:
    op.execute(_RESOLVE.format(expiry=""))
    op.drop_column("api_tokens", "expires_at")
