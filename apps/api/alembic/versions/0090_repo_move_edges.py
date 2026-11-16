"""Add repo_move_edges: remembered ``old -> new`` repository names, keyed by GitHub's stable repo id.

``repository.renamed``/``transferred`` webhooks rewrite waivers and automation opt-ins (keyed by name strings).
Two cases could not be handled from the payload alone: moves delivered out of order (b->c before a->b left
rows at ``b``), and a transfer that also renames (its payload has no ``changes.repository``, so the old name
was unknown). Each move is now stored as an edge; the handler follows edges from the old name to the final one
and, for a transfer, takes the old name from the repo's last recorded move.

Additive: nothing existing is touched and nothing is backfilled. Tenant-isolated with the usual RLS policy;
only the API reads and writes it (no DELETE: the uninstall purge is the SECURITY DEFINER function, which this
migration redefines, as 0080 did, to clear the table). Downgrade drops the table: moves delivered out of order
or transfer-and-renames are then not reconciled, as before.

Revision ID: 0090
Revises: 0089
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"

# 0080's table list (0069's plus security_alert_syncs); the purge function is replaced with and without this table.
_PURGED = (
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "activity_sync_cursors",
    "org_membership_sync_cursors",
    "automation_repo_settings",
    "repo_event_daily_counts",
    "repo_events",
    "security_alert_syncs",
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
        "repo_move_edges",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("github_repo_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("old_name", sa.String(), primary_key=True),
        sa.Column("new_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(sa.text("ALTER TABLE repo_move_edges ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE repo_move_edges FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON repo_move_edges USING ({_TENANT_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE ON repo_move_edges TO clevis_api;
            END IF;
        END
        $$;
        """
    )
    _replace_purge_function((*_PURGED, "repo_move_edges"))


def downgrade() -> None:
    _replace_purge_function(_PURGED)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE SELECT, INSERT, UPDATE ON repo_move_edges FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON repo_move_edges"))
    op.drop_table("repo_move_edges")
