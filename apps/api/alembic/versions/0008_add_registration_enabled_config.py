"""seed registration_enabled app_config key

Self-hosted instances can now allow open self-registration (POST /auth/register).
The owner can disable it later from Settings -> Instance Configuration; default is
"true" so existing and fresh instances keep working without extra setup.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO app_config (key, value) VALUES ('registration_enabled', 'true') "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM app_config WHERE key = 'registration_enabled'"))
