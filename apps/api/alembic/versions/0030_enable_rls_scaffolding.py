"""Enable RLS + tenant-isolation policies as scaffolding.

ENABLE without FORCE: policies do not apply to the table-owning role.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"

# Strict equality: tenant_id is NOT NULL, or (audit_logs) must hide NULL rows by design.
_STRICT_TABLES = ["memberships", "github_installations", "invitations", "audit_logs"]

# Equality-OR-NULL: tenant_id is nullable by design, so NULL-tenant rows stay visible.
_NULLABLE_TABLES = ["orgs", "saved_tokens", "scan_results"]


def upgrade() -> None:
    for table in _STRICT_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(f"CREATE POLICY tenant_isolation ON {table} USING ({_TENANT_FILTER})")
        )

    for table in _NULLABLE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation ON {table} "
                f"USING (tenant_id IS NULL OR {_TENANT_FILTER}) "
                f"WITH CHECK ({_TENANT_FILTER})"
            )
        )


def downgrade() -> None:
    for table in _STRICT_TABLES + _NULLABLE_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
