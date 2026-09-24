"""Add resolve_installation_tenant_id() SECURITY DEFINER function.

Fixes a live bug: the webhook receiver is unauthenticated (HMAC-verified, not a login
session) so it never sets app.tenant_id/app.user_id, which made github_installations'
RLS policy hide every row from installation.deleted's cleanup delete under the
non-superuser clevis_api role. This function does one narrow SECURITY DEFINER read
(installation_id -> tenant_id) so the handler can set proper session context before the
real delete, instead of granting the API role BYPASSRLS (all-or-nothing for every query).

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-16
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION resolve_installation_tenant_id(p_installation_id integer)
        RETURNS integer
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT tenant_id FROM github_installations
            WHERE installation_id = p_installation_id
            LIMIT 1
        $$;
        """
    )
    # Postgres grants EXECUTE on new functions to PUBLIC by default; revoke it so the
    # GRANT below actually narrows who can call this RLS-bypassing lookup.
    op.execute("REVOKE EXECUTE ON FUNCTION resolve_installation_tenant_id(integer) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT EXECUTE ON FUNCTION resolve_installation_tenant_id(integer) TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS resolve_installation_tenant_id(integer)")
