"""add jobs.heartbeat_at

Issue #215: _reclaim_stale_jobs resets any job stuck in 'processing' for
longer than RECLAIM_TIMEOUT_MINUTES (30 min) back to 'queued', assuming its
worker crashed -- but updated_at is only set once, at claim time, so a
legitimately slow (not crashed) job looks identical to a crashed one once
that window elapses. A second worker can then pick up the same job and
duplicate its external side effect, and depending on timing the job can end
up mislabeled 'failed' even though the original attempt actually succeeded.

heartbeat_at is touched by the worker every ~10s while a job handler is
actually running (apps/worker/src/worker.py's _heartbeat_while_running), so
_reclaim_stale_jobs can check it instead of/alongside updated_at and leave a
genuinely-still-running job alone past 30 minutes.

Nullable, no backfill needed -- additive-only. Same style as
0014_add_jobs_retry_count.py.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "heartbeat_at")
