"""Add waiver_requests (#1274).

One additive table: a member's request for a check waiver, pending until an org admin approves (creating the
``check_waivers`` row it then points at) or denies it. Tenant-isolated with the usual RLS policy (ENABLE and
FORCE). ``clevis_api`` gets SELECT/INSERT/UPDATE/DELETE: DELETE because account deletion
(services/account_deletion.py) issues a ``DELETE ... WHERE tenant_id`` against every tenant table it does not skip.
``requested_by_user_id`` is SET NULL when the requester's account is deleted (the email stays in ``requested_by``);
``waiver_id`` is SET NULL when the waiver an approval created is revoked.

No data to backfill. Downgrade drops the table: pending and decided requests are lost.

Revision ID: 0095
Revises: 0094
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None

_TENANT_FILTER = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::int"


def upgrade() -> None:
    op.create_table(
        "waiver_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_id", sa.Text(), nullable=False),
        sa.Column("repos", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("waiver_id", sa.Integer(), sa.ForeignKey("check_waivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_waiver_requests_tenant_id_status", "waiver_requests", ["tenant_id", "status"])
    op.execute(sa.text("ALTER TABLE waiver_requests ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE waiver_requests FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON waiver_requests USING ({_TENANT_FILTER}) "
            f"WITH CHECK ({_TENANT_FILTER})"
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON waiver_requests TO clevis_api;
                GRANT USAGE, SELECT ON SEQUENCE waiver_requests_id_seq TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("waiver_requests")
