"""stop tenant-gating orgs/saved_tokens/scan_results reads; loosen WITH CHECK for NULL tenant_id

orgs.tenant_id and saved_tokens.tenant_id can legitimately stay NULL permanently (a
brand-new org before its tenant link completes; a legacy saved token for an org Clevis
has never connected) -- migration 0030's strict-equality WITH CHECK rejected those writes
once actually enforced under a non-owner role. This loosens WITH CHECK to allow NULL.
scan_results is not touched: both its write paths always pass a real tenant_id.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"

# orgs/saved_tokens: WITH CHECK loosened too -- both have permanent NULL-tenant write paths.
_LOOSENED_WITH_CHECK_TABLES = ["orgs", "saved_tokens"]

# scan_results: USING loosened (history reads span tenants; the router enforces access).
# WITH CHECK stays strict: every write path passes a real tenant_id.
_STRICT_WITH_CHECK_TABLES = ["scan_results"]


def upgrade() -> None:
    for table in _LOOSENED_WITH_CHECK_TABLES:
        op.execute(sa.text(f"DROP POLICY tenant_isolation ON {table}"))
        op.execute(
            sa.text(f"CREATE POLICY tenant_isolation ON {table} USING (true) WITH CHECK (tenant_id IS NULL OR {_TENANT_FILTER})")
        )
    for table in _STRICT_WITH_CHECK_TABLES:
        op.execute(sa.text(f"DROP POLICY tenant_isolation ON {table}"))
        op.execute(sa.text(f"CREATE POLICY tenant_isolation ON {table} USING (true) WITH CHECK ({_TENANT_FILTER})"))


def downgrade() -> None:
    for table in _LOOSENED_WITH_CHECK_TABLES:
        op.execute(sa.text(f"DROP POLICY tenant_isolation ON {table}"))
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation ON {table} "
                f"USING (tenant_id IS NULL OR {_TENANT_FILTER}) "
                f"WITH CHECK ({_TENANT_FILTER})"
            )
        )
    for table in _STRICT_WITH_CHECK_TABLES:
        op.execute(sa.text(f"DROP POLICY tenant_isolation ON {table}"))
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation ON {table} "
                f"USING (tenant_id IS NULL OR {_TENANT_FILTER}) "
                f"WITH CHECK ({_TENANT_FILTER})"
            )
        )
