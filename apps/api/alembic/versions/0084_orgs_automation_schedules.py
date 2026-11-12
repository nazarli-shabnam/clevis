"""Add orgs.pr_nudge_schedule and orgs.dependabot_triage_schedule: per-org overrides for unattended runs.

Stale-PR nudges and Dependabot auto-triage were manual only. They can now run on a schedule
(``pr_nudge_cadence`` / ``dependabot_triage_cadence``, off by default). Each org may override its instance
setting with the nullable-override pattern of ``orgs.scheduled_scans`` (0062): NULL follows the instance,
true runs the automation (weekly when the instance cadence is off), false never does.

Schema change: two nullable boolean columns, no default, no backfill, so every existing org keeps behaving
exactly as before (nothing runs unattended). Downgrade drops both columns (the per-org choices are lost).

Revision ID: 0084
Revises: 0083
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("pr_nudge_schedule", sa.Boolean(), nullable=True))
    op.add_column("orgs", sa.Column("dependabot_triage_schedule", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "dependabot_triage_schedule")
    op.drop_column("orgs", "pr_nudge_schedule")
