"""Add repo_event_daily_counts table + clevis_worker/clevis_api grants.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "repo_event_daily_counts",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("tenant_id", "repo", "event_type", "day", name="pk_repo_event_daily_counts"),
    )
    op.create_index("ix_repo_event_daily_counts_tenant_id_day", "repo_event_daily_counts", ["tenant_id", "day"])

    op.execute(sa.text("ALTER TABLE repo_event_daily_counts ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON repo_event_daily_counts "
            f"USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, INSERT, UPDATE ON repo_event_daily_counts TO clevis_worker;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE ON repo_event_daily_counts TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE ON repo_event_daily_counts FROM clevis_worker;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE SELECT, INSERT, UPDATE ON repo_event_daily_counts FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON repo_event_daily_counts"))
    op.drop_table("repo_event_daily_counts")
