"""Add pending_installations: GitHub App installations Clevis was told about but nobody has connected yet.

When an organization member only *requests* the App, an owner approves on GitHub, and GitHub redirects the
owner's browser to the Setup URL. If that owner is not a Clevis user (or never reaches the redirect), nothing
ever tells Clevis the installation exists, and the requester waits on a Settings page that stays empty.
``installation.created`` is now recorded here (GitHub id of the account, the approving ``sender`` and the
``requester``), and Settings offers it to those two people: the approver can connect it with the existing,
fully-checked ``POST /orgs/{org}/installations/sync`` (the row is removed once that succeeds), the requester is
told what is still needed. Nothing here grants access or creates an org: this table is only a notice board.

Schema change: one new table, no existing data touched and nothing backfilled. Not tenant data (no tenant exists
until someone connects the org), so no RLS, like ``webhook_deliveries``; every API read is filtered by the
authenticated user's GitHub id. The API role gets SELECT/INSERT/UPDATE/DELETE; the worker never touches it.
Rows older than 30 days are dropped whenever a new one is recorded, and on ``installation.deleted``.
Downgrade drops the table (the Settings notice then just has nothing to show).

Revision ID: 0086
Revises: 0085
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_installations",
        sa.Column("installation_id", sa.BigInteger(), primary_key=True),
        sa.Column("account_login", sa.Text(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_github_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_login", sa.Text(), nullable=True),
        sa.Column("requester_github_id", sa.BigInteger(), nullable=True),
        sa.Column("requester_login", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_pending_installations_sender", "pending_installations", ["sender_github_id"])
    op.create_index("ix_pending_installations_requester", "pending_installations", ["requester_github_id"])
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON pending_installations TO clevis_api;
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
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE SELECT, INSERT, UPDATE, DELETE ON pending_installations FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_pending_installations_requester", table_name="pending_installations")
    op.drop_index("ix_pending_installations_sender", table_name="pending_installations")
    op.drop_table("pending_installations")
