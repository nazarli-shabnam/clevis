"""Drop the legacy org_memberships table.

SCHEMA CHANGE -- DROPs a table; not automatically reversible with data. Safe because
`memberships` has held an equivalent row for every org_memberships row since the
dual-write that superseded it, and nothing else references org_memberships.id.
downgrade() recreates the table structure and grants but cannot restore the dropped rows.

Revision ID: 0045
Revises: 0044
Create Date: 2026-09-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cascades to its sequence, constraints, FKs and grants; it never had RLS policies.
    op.drop_table("org_memberships")


def downgrade() -> None:
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
    # The clevis_api role only exists when API_DB_PASSWORD is configured.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON org_memberships TO clevis_api;
                GRANT USAGE, SELECT ON org_memberships_id_seq TO clevis_api;
              END IF;
            END
            $$;
            """
        )
    )
    # The org_memberships rows themselves are not restored.
