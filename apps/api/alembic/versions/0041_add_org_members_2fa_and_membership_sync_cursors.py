"""Add org_members.two_factor_enabled + org_membership_sync_cursors.

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.add_column("org_members", sa.Column("two_factor_enabled", sa.Boolean(), nullable=True))

    op.create_table(
        "org_membership_sync_cursors",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("org_login", sa.String(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()
        ),
    )

    op.execute(sa.text("ALTER TABLE org_membership_sync_cursors ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON org_membership_sync_cursors "
            f"USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, INSERT, UPDATE ON org_membership_sync_cursors TO clevis_worker;
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
                GRANT SELECT, INSERT, UPDATE ON org_membership_sync_cursors TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE ON org_membership_sync_cursors FROM clevis_worker;
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
                REVOKE SELECT, INSERT, UPDATE ON org_membership_sync_cursors FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON org_membership_sync_cursors"))
    op.drop_table("org_membership_sync_cursors")
    op.drop_column("org_members", "two_factor_enabled")
