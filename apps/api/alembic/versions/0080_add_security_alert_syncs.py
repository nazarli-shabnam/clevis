"""Add security_alert_syncs: per (repo, kind) record that the alert backfill read a repo's open alerts in full.

``security_alerts`` was filled only by webhook events, so alerts already open when the App was installed were
invisible, and the Security matrix could not tell "no alerts" from "not yet ingested" for a repo that had a
few rows. The new ``github.backfill_security_alerts`` worker job reads each repo's open Dependabot, code
scanning and secret scanning alerts and records here, per repo and kind, that the list was read completely;
the matrix uses the stored rows only for (repo, kind) pairs recorded here and otherwise asks GitHub live.

It also redefines ``purge_tenant_github_data`` (0069) to delete this table's rows too, so removing the last
installation clears the completeness record along with the alerts it vouched for: a reinstall must not trust a
record whose rows were purged. Otherwise additive (nothing existing is touched, nothing is backfilled by the
migration; the job does that). Tenant-isolated with the usual RLS policy, like the other sync cursor tables. The worker writes it and
the API reads it; like the other sync cursor tables (0041) the API role also gets INSERT/UPDATE, which the API
tests need to seed it under the restricted role (no DELETE: the uninstall purge is the SECURITY DEFINER function). Downgrade drops the table: the matrix then falls back to
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

# 0069's table list; the purge function is replaced (keeping its grants) with and without this migration's table.
_PURGED = (
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "activity_sync_cursors",
    "org_membership_sync_cursors",
    "automation_repo_settings",
    "repo_event_daily_counts",
    "repo_events",
)


def _replace_purge_function(tables: tuple[str, ...]) -> None:
    deletes = "\n".join(f"    DELETE FROM {t} WHERE tenant_id = p_tenant_id;" for t in tables)
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION purge_tenant_github_data(p_tenant_id integer)
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::int THEN
                RAISE EXCEPTION 'purge_tenant_github_data: session tenant does not match %', p_tenant_id
                    USING ERRCODE = '42501';
            END IF;
            IF EXISTS (SELECT 1 FROM github_installations WHERE tenant_id = p_tenant_id) THEN
                RETURN;
            END IF;
{deletes}
        END
        $$;
        """
    )


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
                GRANT SELECT, INSERT, UPDATE ON security_alert_syncs TO clevis_api;
            END IF;
        END
        $$;
        """
    )
    # SECURITY DEFINER: the function runs as its owner, so it needs no DELETE grant on the new table.
    _replace_purge_function((*_PURGED, "security_alert_syncs"))


def downgrade() -> None:
    _replace_purge_function(_PURGED)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                REVOKE SELECT, INSERT, UPDATE ON security_alert_syncs FROM clevis_worker;
            END IF;
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE SELECT, INSERT, UPDATE ON security_alert_syncs FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON security_alert_syncs"))
    op.drop_table("security_alert_syncs")
