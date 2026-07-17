"""add jobs.retry_count

The worker previously had no way to detect or bound a job that gets stuck in
'processing' forever after its worker crashes, and no bounded retry for jobs
that fail due to a transient error (network blip, GitHub 5xx). Both cases now
share this single counter: the worker's reclaim sweep increments it when
resetting a stale 'processing' job back to 'queued', and process_job
increments it when requeueing after a transient failure — either path marks
the job 'failed' once the shared cap is exceeded, so a job that repeatedly
fails (whichever reason) can't retry indefinitely.

Uses a DDL default so existing rows are backfilled to 0 in the same
add_column statement — jobs is a low-volume queue table, so a two-step
add-then-backfill isn't needed here.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("jobs", "retry_count")
