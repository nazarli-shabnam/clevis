"""remove github_api_base and cors_origins app_config keys

Both are deploy-time settings now read from env vars (GITHUB_API_BASE, CORS_ORIGINS):
cors_origins is a CORS security boundary read once at startup, and github_api_base is
where GitHub tokens are sent — neither belongs in runtime admin-editable config. Only
worker_poll_seconds remains in app_config.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM app_config WHERE key IN ('github_api_base', 'cors_origins')")
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO app_config (key, value) VALUES "
            "('github_api_base', 'https://api.github.com'), "
            "('cors_origins', '[\"*\"]') "
            "ON CONFLICT (key) DO NOTHING"
        )
    )
