"""SECURITY DEFINER reads for the pending-invite lookup and the workspace-admin audit log.

Under the restricted clevis_api role, migration 0030's policy on `invitations` and `audit_logs`
is `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int` with no self-access
clause, and two reads run with no tenant context at all:

  * login's "pending invitations for this email" is cross-tenant by nature (the user belongs to
    no org yet), so it always returned [].
  * `GET /audit` and `GET /audit/actions` are workspace-admin, instance-wide views, so the admin
    always saw an empty log.

Neither has a tenant to set, so both go through narrow SECURITY DEFINER functions (pattern of
0035/0047). The workspace-admin / verified-email checks stay in the API layer, as before. Each
function is REVOKEd from PUBLIC and granted only to clevis_api.

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-30
"""

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

_FUNCTIONS = (
    "pending_invitations_for_email(text)",
    "audit_logs_for_admin(text, integer)",
    "audit_log_actions()",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pending_invitations_for_email(p_email text)
        RETURNS SETOF invitations
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT * FROM invitations
            WHERE lower(email) = lower(p_email)
              AND status = 'pending'
              AND expires_at > now()
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_logs_for_admin(p_action text, p_limit integer)
        RETURNS SETOF audit_logs
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT * FROM audit_logs
            WHERE p_action IS NULL OR action = p_action
            ORDER BY id DESC
            LIMIT p_limit
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_log_actions()
        RETURNS SETOF text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT DISTINCT action FROM audit_logs ORDER BY action
        $$;
        """
    )
    for fn in _FUNCTIONS:
        # Postgres grants EXECUTE on new functions to PUBLIC by default; revoke it so the
        # GRANT below actually narrows who can call these RLS-bypassing reads.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC")
    grants = "\n".join(f"GRANT EXECUTE ON FUNCTION {fn} TO clevis_api;" for fn in _FUNCTIONS)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                {grants}
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    for fn in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
