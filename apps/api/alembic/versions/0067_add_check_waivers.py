"""Add check_waivers -- accepted-risk exceptions for security checks, per tenant.

A waiver says "we know this check fails and accept it until <date>": it records the check, an optional
list of repos (NULL = the whole org), a required reason and owner, and a mandatory expiry. While active,
a failing check is excluded from the score like ``not_applicable`` but stays visible (the scan stamps it
``waived``). ``expiry_noted_at`` is set once the expiry sweep has audited/alerted a lapsed waiver, so it
fires once.

Tenant-isolated with the same RLS policy as the other tenant tables (ENABLE + FORCE), and the API role
gets full DML plus the id sequence (the worker is not granted anything). Pure additive create_table: no
existing data is touched, downgrade drops the table (and with it any waivers).

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "check_waivers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_id", sa.Text(), nullable=False),
        sa.Column("repos", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expiry_noted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_check_waivers_tenant_id_check_id", "check_waivers", ["tenant_id", "check_id"])
    op.execute(sa.text("ALTER TABLE check_waivers ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE check_waivers FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON check_waivers USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON check_waivers TO clevis_api;
                GRANT USAGE, SELECT ON SEQUENCE check_waivers_id_seq TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON check_waivers"))
    op.drop_table("check_waivers")
