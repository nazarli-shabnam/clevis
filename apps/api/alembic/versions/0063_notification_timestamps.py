"""Add security_alerts.ingested_at and github_installations.permissions_changed_at.

The notification feed (``routers/org_notifications.py``) decides what is new and what is read by comparing an
item's time with the user's "read up to" marker. Two of its sources used a time that is wrong for that:

* ``security_alerts.created_at`` is GitHub's alert time. A webhook that arrives late, or a backfill, inserts
  an alert whose ``created_at`` is before the user's marker, so it was born "read". ``ingested_at`` is when
  Clevis first stored the row; the worker's upsert never touches it (its UPDATE list names other columns).
* ``github_installations.permissions_synced_at`` moves on every sync, including ones that change nothing, so
  a "permission drift" item reappeared as unread after each refresh or webhook redelivery.
  ``permissions_changed_at`` moves only when the granted permissions actually differ.

Existing rows are backfilled so nothing becomes newly unread: ``ingested_at`` = ``created_at`` and
``permissions_changed_at`` = ``permissions_synced_at``. The backfill UPDATEs run as the migration role
(the superuser credential in a normal deployment, which is not subject to the tables' row-level security).
The ``security_alerts`` backfill rewrites every row once, which is quick at this table's size.
No drops; downgrade removes both columns (losing only the new timestamps). ``clevis_api``/``clevis_worker``
hold table-level grants on both tables (0039/0044), which cover the new columns.

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "security_alerts",
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(sa.text("UPDATE security_alerts SET ingested_at = LEAST(created_at, now())"))
    op.add_column("github_installations", sa.Column("permissions_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE github_installations SET permissions_changed_at = permissions_synced_at"))


def downgrade() -> None:
    op.drop_column("github_installations", "permissions_changed_at")
    op.drop_column("security_alerts", "ingested_at")
