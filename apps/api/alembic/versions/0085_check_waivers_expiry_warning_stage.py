"""Add check_waivers.expiry_warning_stage: which "about to expire" warnings were already announced.

The waiver-expiry sweep warns org admins (bell item and ``waiver_expiring`` alert destinations) 7 days and
1 day before a waiver lapses. This column records how many of those windows were announced for the waiver's
current expiry (0 = none, 1 = the 7-day one, 2 = the 1-day one) so each is announced once, across restarts
and replicas. Creating or renewing a waiver sets it to the windows the new expiry is already inside.

Schema change: one NOT NULL smallint column with a server default of 0, so existing rows are valid at once and
nothing is rewritten by hand. Existing waivers that are already inside a window at upgrade time are warned
once by the next sweep tick; that is the intended behaviour (they are about to lapse), and waivers are capped
per organization so it is a handful of messages at most. Downgrade drops the column; nothing else reads it.

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "check_waivers",
        sa.Column("expiry_warning_stage", sa.SmallInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("check_waivers", "expiry_warning_stage")
