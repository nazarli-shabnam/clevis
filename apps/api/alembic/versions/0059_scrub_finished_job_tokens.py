"""Scrub the encrypted GitHub token from jobs that already finished (issue #541).

Cache-clear, backfill and membership jobs carry a Fernet-encrypted GitHub token in ``jobs.payload``
(``{"token": "v2:..."}``). The worker never removed it, so every finished job kept a recoverable
credential for anyone with DB read access plus ``JOB_SECRET_KEY`` (a backup, for instance).

Adds ``jobs_scrub_token(text)``, which drops the top-level ``token`` key and returns the payload
unchanged otherwise (a payload that is not valid JSON becomes ``{}``: nothing in it is needed once
a job is finished, and it cannot be parsed to remove just the token). The worker calls it
whenever a job reaches ``done`` or ``failed``. This revision also applies it once to every existing
``done``/``failed`` row.

DATA LOSS (intended, irreversible): the token is removed from already-finished rows and cannot be
restored by ``downgrade``. Nothing reads a finished job's token. ``queued``/``processing`` rows are
not touched, since the worker still needs their token. No table or column changes; the function is
executable by PUBLIC (the default), so ``clevis_worker`` needs no new grant.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-06
"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION jobs_scrub_token(p text) RETURNS text
        LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            RETURN (p::jsonb - 'token')::text;
        EXCEPTION WHEN others THEN
            RETURN '{}';
        END;
        $$
        """
    )
    op.execute(
        "UPDATE jobs SET payload = jobs_scrub_token(payload) "
        "WHERE status IN ('done', 'failed') AND payload LIKE '%token%'"
    )


def downgrade() -> None:
    # The scrubbed tokens are gone for good; only the function is removed.
    op.execute("DROP FUNCTION IF EXISTS jobs_scrub_token(text)")
