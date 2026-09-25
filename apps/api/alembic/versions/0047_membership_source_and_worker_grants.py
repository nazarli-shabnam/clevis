"""Add memberships.source, worker membership-revoke grants, invitation tenant lookup fn.

memberships.source records how a membership was granted: 'github' (derived from verified
GitHub org status -- OAuth login sync, App install, PAT auto-link) or 'invite' (accepted
Clevis invitation). GitHub-driven reconciliation (login sync, the worker's member_removed
handler) only ever demotes/deletes 'github' rows, so invite-granted access for non-org
members survives login, and GitHub org removal revokes GitHub-derived access.

Backfill is best-effort: a row becomes 'invite' when an accepted invitation for that org
matches the user's email (case-insensitive); everything else keeps the 'github' default,
which is exactly today's behavior for that row.

invitation_tenant_by_token() lets unauthenticated-tenant invitation preview/accept set
tenant context under the non-owner clevis_api role (pattern of 0035).

Revision ID: 0047
Revises: 0046
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("source", sa.Text(), nullable=False, server_default="github"),
    )
    op.create_check_constraint(
        "ck_memberships_source", "memberships", "source IN ('github', 'invite')"
    )
    op.execute(
        """
        UPDATE memberships m SET source = 'invite'
        FROM tenants t, users u, invitations i
        WHERE t.id = m.tenant_id AND t.kind = 'org'
          AND u.id = m.user_id
          AND i.org_id = t.org_id AND i.status = 'accepted'
          AND lower(i.email) = lower(u.email)
        """
    )
    op.execute(
        """
        CREATE FUNCTION invitation_tenant_by_token(p_token text)
        RETURNS integer
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT tenant_id FROM invitations WHERE token = p_token LIMIT 1
        $$;
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION invitation_tenant_by_token(text) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT EXECUTE ON FUNCTION invitation_tenant_by_token(text) TO clevis_api;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT ON users TO clevis_worker;
                GRANT SELECT, DELETE ON memberships TO clevis_worker;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                REVOKE SELECT ON users FROM clevis_worker;
                REVOKE SELECT, DELETE ON memberships FROM clevis_worker;
            END IF;
        END
        $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS invitation_tenant_by_token(text)")
    op.drop_constraint("ck_memberships_source", "memberships", type_="check")
    op.drop_column("memberships", "source")
