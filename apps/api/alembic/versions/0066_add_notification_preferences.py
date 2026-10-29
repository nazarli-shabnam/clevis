"""Add notification_preferences (per-user, per-org muted notification kinds).

Lets a user hide kinds of in-app notifications (critical_alert, score_drop, job_failed, permission_drift)
they don't care about; the feed and its unread count then leave them out server-side. One row per
(user, tenant); no row means nothing is muted. A separate table rather than a column on
``notification_reads``: that row's ``last_read_at`` is NOT NULL and only exists once a user has marked
something read, whereas a preference can be set before then.

Tenant-isolated with the usual RLS policy (it is read and written inside the org's tenant context), like
``notification_reads``. Pure additive create_table: no existing data is touched and downgrade just drops it.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("muted_kinds", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.execute(sa.text("ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE notification_preferences FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON notification_preferences USING ({_TENANT_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON notification_preferences TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON notification_preferences"))
    op.drop_table("notification_preferences")
