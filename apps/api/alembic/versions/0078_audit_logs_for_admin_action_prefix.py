"""Action-prefix filter for the instance-wide audit read.

``GET /audit`` (0075) filters by exact action only, while the org-scoped audit view also takes an
action prefix (e.g. ``token.``). This adds an overload of ``audit_logs_for_admin_filtered`` with an
extra ``p_action_prefix`` LIKE pattern (built and escaped by the caller; ``ESCAPE '\\'`` makes a literal
``%`` or ``_`` match itself). The 7-argument function from 0075 is left in place so a mixed-version
rollout (old API instances still calling it) keeps working. Additive only: no table or data changes.

Revision ID: 0078
Revises: 0077
"""

from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

_FN = (
    "audit_logs_for_admin_filtered(text, text, text, text, timestamptz, timestamptz, integer, integer)"
)


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION audit_logs_for_admin_filtered(
            p_action text,
            p_action_prefix_like text,
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
              AND (p_action_prefix_like IS NULL OR action LIKE p_action_prefix_like ESCAPE '\')
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
