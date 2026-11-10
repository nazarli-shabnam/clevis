"""Add security_alert_syncs: per (repo, kind) record that the alert backfill read a repo's open alerts in full.

``security_alerts`` was filled only by webhook events, so alerts already open when the App was installed were
invisible, and the Security matrix could not tell "no alerts" from "not yet ingested" for a repo that had a
few rows. The new ``github.backfill_security_alerts`` worker job reads each repo's open Dependabot, code
scanning and secret scanning alerts and records here, per repo and kind, that the list was read completely;
the matrix uses the stored rows only for (repo, kind) pairs recorded here and otherwise asks GitHub live.

Pure additive create_table (nothing existing is touched, nothing is backfilled by the migration; the job does
that). Tenant-isolated with the usual RLS policy, like the other sync cursor tables. The worker writes it and
the API reads it, so each gets only what it needs. Downgrade drops the table: the matrix then falls back to
its live GitHub reads, as before.

Revision ID: 0080
Revises: 0079
Create Date: 2026-11-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "security_alert_syncs",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("repo", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), primary_key=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(sa.text("ALTER TABLE security_alert_syncs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE security_alert_syncs FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON security_alert_syncs USING ({_TENANT_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, INSERT, UPDATE ON security_alert_syncs TO clevis_worker;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT ON security_alert_syncs TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE ON security_alert_syncs FROM clevis_worker;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE SELECT ON security_alert_syncs FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON security_alert_syncs"))
    op.drop_table("security_alert_syncs")
