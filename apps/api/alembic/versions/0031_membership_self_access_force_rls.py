"""Widen memberships/github_installations RLS with self-access + enable FORCE.

Every write to these tables outside require_org_role/require_personal_tenant (org-admin
bootstrap, invite accept, OAuth provisioning, pre-login setup/register) is a self-write:
row.user_id / owner_user_id always equals the caller. The added self-access clause lets a
user read/write their own row regardless of tenant session context, without weakening the
tenant_id-equality half of the policy that actually prevents cross-tenant access.

CAVEAT: in the default docker-compose deployment DB_USER is a Postgres superuser, which
unconditionally bypasses RLS -- so FORCE has no effect until an operator opts into the
non-superuser clevis_api role (API_DB_PASSWORD; see docs/self-hosting.md). Correct and
inert-but-harmless until then.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"
_USER_FILTER = "user_id = NULLIF(current_setting('app.user_id', true), '')::int"
_OWNER_USER_FILTER = "owner_user_id = NULLIF(current_setting('app.user_id', true), '')::int"

_FORCE_TABLES = ["memberships", "github_installations", "scan_results"]


def upgrade() -> None:
    op.execute(sa.text("DROP POLICY tenant_isolation ON memberships"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON memberships "
            f"USING ({_TENANT_FILTER} OR {_USER_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER} OR {_USER_FILTER})"
        )
    )

    op.execute(sa.text("DROP POLICY tenant_isolation ON github_installations"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON github_installations "
            f"USING ({_TENANT_FILTER} OR {_OWNER_USER_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER} OR {_OWNER_USER_FILTER})"
        )
    )

    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))

    op.execute(sa.text("DROP POLICY tenant_isolation ON github_installations"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation ON github_installations "
            f"USING ({_TENANT_FILTER})"
        )
    )

    op.execute(sa.text("DROP POLICY tenant_isolation ON memberships"))
    op.execute(sa.text(f"CREATE POLICY tenant_isolation ON memberships USING ({_TENANT_FILTER})"))
