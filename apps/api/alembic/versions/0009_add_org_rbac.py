"""add multi-tenant org RBAC: orgs, org_memberships, invitations

Introduces the org/member/individual model. `users.is_owner` becomes
`users.is_workspace_admin` (same semantics: the instance host). `github_installations`
gains `org_id` / `owner_user_id` (exactly one set per row) to scope each installation to
either a connected GitHub org or an individual's personal account.

Existing `github_installations` rows are backfilled best-effort: organization installs
each get a new `Org` row (github_org_id left NULL — GitHub's numeric org id was never
recorded historically; it fills in lazily the next time an org member authenticates and
the GitHub membership check runs) plus an admin `OrgMembership` for the current workspace
admin, since there's no historical record of who actually connected each installation.
User installs get `owner_user_id` set to the workspace admin for the same reason.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "is_owner", new_column_name="is_workspace_admin")

    op.create_table(
        "orgs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("github_org_id", sa.Integer, nullable=True, unique=True),
        sa.Column("github_login", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "org_memberships",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_memberships_org_user"),
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("token", sa.String, nullable=False, unique=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("invited_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("github_installations", sa.Column("org_id", sa.Integer(), sa.ForeignKey("orgs.id"), nullable=True))
    op.add_column(
        "github_installations", sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True)
    )

    # Backfill: attribute every existing installation to the current workspace admin,
    # since there's no historical record of who actually connected each one.
    op.execute(
        sa.text(
            "INSERT INTO orgs (github_login) "
            "SELECT DISTINCT account_login FROM github_installations "
            "WHERE account_type = 'Organization' "
            "ON CONFLICT (github_login) DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO org_memberships (org_id, user_id, role) "
            "SELECT o.id, u.id, 'admin' "
            "FROM orgs o, (SELECT id FROM users WHERE is_workspace_admin = true ORDER BY id LIMIT 1) u "
            "ON CONFLICT (org_id, user_id) DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "UPDATE github_installations gi SET org_id = o.id "
            "FROM orgs o WHERE gi.account_type = 'Organization' AND gi.account_login = o.github_login"
        )
    )
    op.execute(
        sa.text(
            "UPDATE github_installations SET owner_user_id = "
            "(SELECT id FROM users WHERE is_workspace_admin = true ORDER BY id LIMIT 1) "
            "WHERE account_type = 'User'"
        )
    )

    op.create_check_constraint(
        "ck_github_installations_org_xor_owner",
        "github_installations",
        "(org_id IS NOT NULL AND owner_user_id IS NULL) OR (org_id IS NULL AND owner_user_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_github_installations_org_xor_owner", "github_installations", type_="check")
    op.drop_column("github_installations", "owner_user_id")
    op.drop_column("github_installations", "org_id")
    op.drop_table("invitations")
    op.drop_table("org_memberships")
    op.drop_table("orgs")
    op.alter_column("users", "is_workspace_admin", new_column_name="is_owner")
