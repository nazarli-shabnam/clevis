"""Add security_alerts table + clevis_worker/clevis_api grants.

One polymorphic table for dependabot/code-scanning/secret-scanning alerts (`kind`
discriminates, `details` JSONB holds the kind-specific remainder) rather than three,
since a per-repo "all open alerts" query naturally wants one table. Upsert key is
(tenant_id, repo, kind, number); unlike repo_events's ON CONFLICT DO NOTHING, an alert's
state changes over its lifetime, so a redelivered webhook does ON CONFLICT DO UPDATE.

Revision ID: 0039
Revises: 0038
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "security_alerts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("details", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_security_alerts_tenant_id", "security_alerts", ["tenant_id"])
    op.create_index("ix_security_alerts_tenant_id_repo", "security_alerts", ["tenant_id", "repo"])
    op.create_unique_constraint(
        "uq_security_alerts_tenant_repo_kind_number", "security_alerts", ["tenant_id", "repo", "kind", "number"]
    )

    op.execute(sa.text("ALTER TABLE security_alerts ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON security_alerts "
            f"USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"
        )
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, INSERT, UPDATE ON security_alerts TO clevis_worker;
                GRANT USAGE, SELECT ON security_alerts_id_seq TO clevis_worker;
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
                GRANT SELECT, INSERT, UPDATE ON security_alerts TO clevis_api;
                GRANT USAGE, SELECT ON security_alerts_id_seq TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE ON security_alerts FROM clevis_worker;
                REVOKE USAGE, SELECT ON security_alerts_id_seq FROM clevis_worker;
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
                REVOKE SELECT, INSERT, UPDATE ON security_alerts FROM clevis_api;
                REVOKE USAGE, SELECT ON security_alerts_id_seq FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON security_alerts"))
    op.drop_table("security_alerts")
