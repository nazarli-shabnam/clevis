"""Add org_members + repo_collaborators tables + grants.

repo_collaborators.source is 'direct' only; team-based access is not modeled yet.

org_members.role is captured at add-time from the webhook payload; no GitHub webhook
event covers a role changing afterward, so this column can go stale for an existing
member until a reconciliation poll corrects it -- it is not always current GitHub state.

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "org_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("login", sa.String(), nullable=False),
        sa.Column("avatar_url", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="member"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_org_members_tenant_id", "org_members", ["tenant_id"])
    op.create_unique_constraint("uq_org_members_tenant_login", "org_members", ["tenant_id", "login"])

    op.create_table(
        "repo_collaborators",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("login", sa.String(), nullable=False),
        sa.Column("permission", sa.String(), nullable=False),
        # 'direct' only; team-based access is not modeled yet.
        sa.Column("source", sa.String(), nullable=False, server_default="direct"),
        # NULL = not yet known; the `member` event alone can't determine org membership.
        sa.Column("is_outside_collaborator", sa.Boolean(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_repo_collaborators_tenant_id", "repo_collaborators", ["tenant_id"])
    op.create_index("ix_repo_collaborators_tenant_id_repo", "repo_collaborators", ["tenant_id", "repo"])
    op.create_unique_constraint(
        "uq_repo_collaborators_tenant_repo_login", "repo_collaborators", ["tenant_id", "repo", "login"]
    )

    for table in ("org_members", "repo_collaborators"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(f"CREATE POLICY tenant_isolation ON {table} USING ({_TENANT_FILTER}) WITH CHECK ({_TENANT_FILTER})")
        )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON org_members TO clevis_worker;
                GRANT USAGE, SELECT ON org_members_id_seq TO clevis_worker;
                GRANT SELECT, INSERT, UPDATE, DELETE ON repo_collaborators TO clevis_worker;
                GRANT USAGE, SELECT ON repo_collaborators_id_seq TO clevis_worker;
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
                GRANT SELECT, INSERT, UPDATE, DELETE ON org_members TO clevis_api;
                GRANT USAGE, SELECT ON org_members_id_seq TO clevis_api;
                GRANT SELECT, INSERT, UPDATE, DELETE ON repo_collaborators TO clevis_api;
                GRANT USAGE, SELECT ON repo_collaborators_id_seq TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE, DELETE ON org_members FROM clevis_worker;
                REVOKE USAGE, SELECT ON org_members_id_seq FROM clevis_worker;
                REVOKE SELECT, INSERT, UPDATE, DELETE ON repo_collaborators FROM clevis_worker;
                REVOKE USAGE, SELECT ON repo_collaborators_id_seq FROM clevis_worker;
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
                REVOKE SELECT, INSERT, UPDATE, DELETE ON org_members FROM clevis_api;
                REVOKE USAGE, SELECT ON org_members_id_seq FROM clevis_api;
                REVOKE SELECT, INSERT, UPDATE, DELETE ON repo_collaborators FROM clevis_api;
                REVOKE USAGE, SELECT ON repo_collaborators_id_seq FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    for table in ("org_members", "repo_collaborators"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.drop_table("repo_collaborators")
    op.drop_table("org_members")
