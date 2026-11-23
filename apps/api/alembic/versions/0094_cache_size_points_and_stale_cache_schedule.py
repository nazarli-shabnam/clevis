"""Add cache_size_points and orgs.stale_cache_clear_schedule (#1290).

Two additive changes for the Actions-cache feature:

* ``cache_size_points``: one row per (tenant, day) with the org's total Actions cache bytes, written by the
  cockpit read so the Overview can chart growth instead of only the current total. Tenant-isolated with the
  usual RLS policy. ``clevis_api`` gets DELETE because account deletion (services/account_deletion.py) issues a
  ``DELETE ... WHERE tenant_id`` against every tenant table it does not skip, so without the grant it would fail
  with a permission error under the RLS-enforcing role. Not purged on app uninstall (the purge function is not
  touched here); the rows are plain numbers and the series simply stops growing.
* ``orgs.stale_cache_clear_schedule``: nullable override of the instance ``stale_cache_clear_cadence`` for the
  new scheduled "clear caches idle for N days" automation, same NULL = follow the instance pattern as
  ``orgs.pr_nudge_schedule`` (0084). No default and no backfill, so nothing runs unattended for any existing org.

Downgrade drops both: the recorded history and the per-org choices are lost.

Revision ID: 0094
Revises: 0091
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0091"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "cache_size_points",
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(sa.text("ALTER TABLE cache_size_points ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE cache_size_points FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON cache_size_points USING ({_TENANT_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON cache_size_points TO clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.add_column("orgs", sa.Column("stale_cache_clear_schedule", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "stale_cache_clear_schedule")
    op.drop_table("cache_size_points")
