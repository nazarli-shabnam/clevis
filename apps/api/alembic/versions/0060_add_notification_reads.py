"""Add notification_reads (per-user, per-org "notifications read up to" marker).

The in-app notification center derives its items from tables that already exist (security_alerts,
scan_results, jobs, github_installations), so the only new state is when each user last marked an
org's notifications read. One row per (user, tenant), tenant-isolated with the usual RLS policy
(it is read and written inside the org's tenant context).

Pure additive create_table: no existing data is touched and downgrade just drops the table.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "notification_reads",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(sa.text("ALTER TABLE notification_reads ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE notification_reads FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON notification_reads USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON notification_reads TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON notification_reads"))
    op.drop_table("notification_reads")
