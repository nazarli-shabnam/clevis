"""Add webhook_deliveries table.

No RLS: the webhook receiver has no tenant context; access is gated by HMAC verification.
Retention/pruning of accumulated payloads is not implemented yet.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("delivery_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("installation_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_webhook_deliveries_tenant_id", "webhook_deliveries", ["tenant_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON webhook_deliveries TO clevis_api;
                GRANT USAGE, SELECT ON SEQUENCE webhook_deliveries_id_seq TO clevis_api;
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
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE USAGE, SELECT ON SEQUENCE webhook_deliveries_id_seq FROM clevis_api;
                REVOKE SELECT, INSERT, UPDATE, DELETE ON webhook_deliveries FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_tenant_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
