"""Filtered, cursor-paged instance-wide audit read for workspace admins.

``GET /audit`` reads through the SECURITY DEFINER function ``audit_logs_for_admin`` (0068) because
``audit_logs`` carries a strict tenant RLS policy and the workspace-admin view has no tenant to set.
That function can only filter by exact action and cap the row count, so the page could not narrow by
person, target or date, or reach rows past the newest 500.

This adds ``audit_logs_for_admin_filtered`` with those filters and a ``before_id`` cursor. The old
function is left in place so a mixed-version rollout (old API instances still calling it) keeps
working. Additive only: no table or data changes. The LIKE patterns are built (and escaped) by the
caller; ``ESCAPE '\\'`` makes a literal ``%`` or ``_`` in a filter match itself.

Revision ID: 0075
Revises: 0074
"""

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

_FN = "audit_logs_for_admin_filtered(text, text, text, timestamptz, timestamptz, integer, integer)"


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION audit_logs_for_admin_filtered(
            p_action text,
            p_actor_like text,
            p_target_like text,
            p_since timestamptz,
            p_until timestamptz,
            p_before_id integer,
            p_limit integer
        )
        RETURNS SETOF audit_logs
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT * FROM audit_logs
            WHERE (p_action IS NULL OR action = p_action)
              AND (p_actor_like IS NULL OR actor ILIKE p_actor_like ESCAPE '\')
              AND (p_target_like IS NULL OR target ILIKE p_target_like ESCAPE '\')
              AND (p_since IS NULL OR created_at >= p_since)
              AND (p_until IS NULL OR created_at < p_until)
              AND (p_before_id IS NULL OR id < p_before_id)
            ORDER BY id DESC
            LIMIT p_limit
        $$;
        """
    )
    # Postgres grants EXECUTE on new functions to PUBLIC by default; revoke it so the grant below
    # actually narrows who can call this RLS-bypassing read.
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_FN} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT EXECUTE ON FUNCTION {_FN} TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_FN}")
