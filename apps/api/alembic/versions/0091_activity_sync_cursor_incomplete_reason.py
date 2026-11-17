"""Record on the activity cursor why a backfill / gap-heal run could not read all of the history.

The event backfill is bounded (3 pages of the public feed, at most 50 private repos with one page each) and the
worker always stamped ``activity_sync_cursors.last_synced_at`` afterwards, so a partial run looked like a full
one and ``repo_events`` and the weekly counts could under-report with nothing to say so. This adds a nullable
``incomplete_reason`` TEXT: NULL means the last run read everything it tried to, a value is a short human reason
("private repositories beyond the first 50 were not read") that the Ingestion health card and the Overview show.

Schema change: one additive nullable column; no existing row is rewritten (they stay NULL, i.e. "not known to be
incomplete", which is what they were). Existing table-level grants for ``clevis_worker`` (SELECT/INSERT/UPDATE)
and ``clevis_api`` cover the new column, so no GRANT is needed. Downgrade drops the column.

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activity_sync_cursors", sa.Column("incomplete_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_sync_cursors", "incomplete_reason")
