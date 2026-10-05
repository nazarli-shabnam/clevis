"""Add api_tokens.expires_at, make resolve_api_token ignore expired tokens, and tie the lookup policy to the table owner.

- ``expires_at`` is a nullable column add: NULL means "never expires", so every existing token keeps
  working exactly as before (no backfill, no data loss).
- ``resolve_api_token`` is replaced in place (same signature, owner and EXECUTE grants are kept by
  ``CREATE OR REPLACE``) with one extra predicate, so an expired token fails resolution the same way
  a revoked one does.
- 0054 let that SECURITY DEFINER function read ``api_tokens`` under FORCE row level security by setting
  ``app.api_token_lookup = 'on'`` and admitting rows through a ``token_lookup`` policy keyed on that
  setting. Any session can set a custom setting, so a non-owner session (the API's own role) could
  ``SET app.api_token_lookup = 'on'`` and read every tenant's token rows. The policy is now keyed on
  identity instead: it admits rows only while ``current_user`` is the table's current owner, which is exactly what
  the function runs as (SECURITY DEFINER) and a non-owner session cannot become. The function no longer
  sets the setting. Because this migration owns the function body, it has to carry both changes: a
  separate migration redefining it would silently drop the expiry predicate (or the reverse).

Revision ID: 0056
Revises: 0054
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0054"
branch_labels = None
depends_on = None

_RESOLVE = """
    CREATE OR REPLACE FUNCTION resolve_api_token(p_hash text)
    RETURNS TABLE (token_id integer, tenant_id integer, org_id integer, org_login text, scope text)
    LANGUAGE sql
    SECURITY DEFINER
    SET search_path = pg_catalog, public{extra_set}
    AS $$
        SELECT t.id, t.tenant_id, o.id, o.github_login, t.scope
        FROM api_tokens t JOIN orgs o ON o.tenant_id = t.tenant_id
        WHERE t.token_hash = p_hash AND t.revoked_at IS NULL{expiry}
        LIMIT 1
    $$;
"""

# Resolved from the catalog on every check (not a name baked in at migration time), so handing the table
# to another owner can't silently make every machine token fail to resolve.
_OWNER_POLICY = (
    "CREATE POLICY token_lookup ON api_tokens FOR SELECT "
    "USING (pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'api_tokens'::regclass)) = current_user)"
)


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(_RESOLVE.format(extra_set="", expiry=" AND (t.expires_at IS NULL OR t.expires_at > now())"))
    op.execute(sa.text("DROP POLICY IF EXISTS token_lookup ON api_tokens"))
    op.execute(sa.text(_OWNER_POLICY))


def downgrade() -> None:
    # Back to the 0054 state: function sets the lookup setting, policy keys on it.
    op.execute(_RESOLVE.format(extra_set="\n    SET app.api_token_lookup = 'on'", expiry=""))
    op.execute(sa.text("DROP POLICY IF EXISTS token_lookup ON api_tokens"))
    op.execute(
        sa.text(
            "CREATE POLICY token_lookup ON api_tokens FOR SELECT USING (current_setting('app.api_token_lookup', true) = 'on')"
        )
    )
    op.drop_column("api_tokens", "expires_at")
