"""Add repo_events table + clevis_worker grants.

Deduplicated by delivery_id (ON CONFLICT DO NOTHING) -- GitHub redelivers the same
X-GitHub-Delivery on retry. tenant_id is NOT NULL by design: the consumer skips rows
with no resolved tenant rather than write one with nothing to scope it to. Grants to
clevis_worker/clevis_api are guarded by `IF EXISTS (SELECT FROM pg_roles ...)` so they
no-op on deployments that haven't opted into those roles.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "repo_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("delivery_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("actor_avatar", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_repo_events_tenant_id", "repo_events", ["tenant_id"])
    op.create_unique_constraint("uq_repo_events_delivery_id", "repo_events", ["delivery_id"])

    op.execute(sa.text("ALTER TABLE repo_events ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"CREATE POLICY tenant_isolation ON repo_events USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})"))

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, INSERT ON repo_events TO clevis_worker;
                GRANT USAGE, SELECT ON repo_events_id_seq TO clevis_worker;
                GRANT SELECT, UPDATE ON webhook_deliveries TO clevis_worker;
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
                GRANT SELECT, INSERT ON repo_events TO clevis_api;
                GRANT USAGE, SELECT ON repo_events_id_seq TO clevis_api;
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
                REVOKE SELECT, INSERT ON repo_events FROM clevis_worker;
                REVOKE USAGE, SELECT ON repo_events_id_seq FROM clevis_worker;
                REVOKE SELECT, UPDATE ON webhook_deliveries FROM clevis_worker;
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
                REVOKE SELECT, INSERT ON repo_events FROM clevis_api;
                REVOKE USAGE, SELECT ON repo_events_id_seq FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON repo_events"))
    op.drop_table("repo_events")
