"""Add api_tokens (scoped, revocable machine tokens) and orgs.badge_enabled (opt-in score badge).

- ``api_tokens`` stores only a SHA-256 hash of each token (tokens are 256-bit random, so a
  plain hash is sufficient), tenant-isolated with the usual RLS policy.
- Machine callers present a token *before* any tenant context exists, and the public badge is
  unauthenticated, so two narrow SECURITY DEFINER lookups (pattern of 0035) do the one read each
  needs instead of widening RLS: ``resolve_api_token(hash)`` and ``public_badge_score(login)``.
- ``orgs.badge_enabled`` is a plain NOT NULL DEFAULT false column add (no backfill, no data risk):
  every org starts opted out.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.add_column("orgs", sa.Column("badge_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        # First characters of the token, shown in listings so a token can be told apart.
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="read"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_tokens_tenant_id", "api_tokens", ["tenant_id"])
    op.execute(sa.text("ALTER TABLE api_tokens ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON api_tokens USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )

    op.execute(
        """
        CREATE FUNCTION resolve_api_token(p_hash text)
        RETURNS TABLE (token_id integer, tenant_id integer, org_id integer, org_login text, scope text)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT t.id, t.tenant_id, o.id, o.github_login, t.scope
            FROM api_tokens t JOIN orgs o ON o.tenant_id = t.tenant_id
            WHERE t.token_hash = p_hash AND t.revoked_at IS NULL
            LIMIT 1
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public_badge_score(p_login text)
        RETURNS integer
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT s.score
            FROM orgs o JOIN scan_results s ON s.tenant_id = o.tenant_id AND lower(s.owner) = lower(o.github_login)
            WHERE lower(o.github_login) = lower(p_login) AND o.badge_enabled
            ORDER BY s.created_at DESC, s.id DESC
            LIMIT 1
        $$;
        """
    )
    for fn in ("resolve_api_token(text)", "public_badge_score(text)"):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON api_tokens TO clevis_api;
                GRANT USAGE, SELECT ON SEQUENCE api_tokens_id_seq TO clevis_api;
                GRANT EXECUTE ON FUNCTION resolve_api_token(text) TO clevis_api;
                GRANT EXECUTE ON FUNCTION public_badge_score(text) TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public_badge_score(text)")
    op.execute("DROP FUNCTION IF EXISTS resolve_api_token(text)")
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON api_tokens"))
    op.drop_table("api_tokens")
    op.drop_column("orgs", "badge_enabled")
