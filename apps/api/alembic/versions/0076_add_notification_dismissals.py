"""Add notification_dismissals (per-user, per-org, per-item "I have dealt with this" marks).

In-app notifications are derived on demand, and read state was a single per-user, per-org cursor
(``notification_reads.last_read_at``): to keep one item for later a user had to leave the whole feed
unread. A dismissal marks exactly one item read, keyed by the item's stable id (``kind:source_row_id``,
the same id the feed already returns), so it does not move the cursor and does not touch any other item.

One row per (user, tenant, item). Tenant-isolated with the usual RLS policy (read and written inside
the org's tenant context), like ``notification_reads`` and ``notification_preferences``. Pure additive
create_table: no existing data is touched, nothing is backfilled, and downgrade just drops it. Rows for
items that have aged out of any feed window are harmless and are pruned when the user next dismisses.

Revision ID: 0076
Revises: 0075
"""

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "notification_dismissals",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_id", sa.Text(), primary_key=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(sa.text("ALTER TABLE notification_dismissals ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE notification_dismissals FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON notification_dismissals USING ({_TENANT_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON notification_dismissals TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON notification_dismissals"))
    op.drop_table("notification_dismissals")
