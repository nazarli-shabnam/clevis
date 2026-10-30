"""Judge invitation expiry by wall-clock time in pending_invitations_for_email.

0068 compared `expires_at` to `now()`, which Postgres freezes at the start of the transaction. A
login transaction that began before an invitation lapsed could therefore still return it. Use
`clock_timestamp()` (and mark the function VOLATILE, since it no longer returns the same result
within a statement) so an invitation that has expired by the time of the lookup is excluded.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-30
"""

from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None

_FN = "pending_invitations_for_email(text)"


def _create(clock: str, volatility: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION pending_invitations_for_email(p_email text)
        RETURNS SETOF invitations
        LANGUAGE sql
        {volatility}
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT * FROM invitations
            WHERE lower(email) = lower(p_email)
              AND status = 'pending'
              AND expires_at > {clock}
        $$;
        """
    )
    # CREATE OR REPLACE keeps the existing ACL; restate it so the intent is explicit.
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_FN} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT EXECUTE ON FUNCTION {_FN} TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    _create("clock_timestamp()", "VOLATILE")


def downgrade() -> None:
    _create("now()", "STABLE")
