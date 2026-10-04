"""Add notification_destinations -- per-tenant Slack/Teams/generic webhook alert targets.

URL and signing secret are Fernet-encrypted by the API before they reach this table, so a
database dump alone doesn't leak working webhook URLs. Tenant-isolated with the same RLS
policy as the other tenant tables; the API role gets full DML (the worker is not granted
anything: only the API sends notifications for now).

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "notification_destinations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("encrypted_url", sa.Text(), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("events", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("min_score_drop", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notification_destinations_tenant_id", "notification_destinations", ["tenant_id"])

    op.execute(sa.text("ALTER TABLE notification_destinations ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON notification_destinations "
            f"USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON notification_destinations TO clevis_api;
                GRANT USAGE, SELECT ON SEQUENCE notification_destinations_id_seq TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE, DELETE ON notification_destinations FROM clevis_api;
                REVOKE USAGE, SELECT ON SEQUENCE notification_destinations_id_seq FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON notification_destinations"))
    op.drop_table("notification_destinations")
