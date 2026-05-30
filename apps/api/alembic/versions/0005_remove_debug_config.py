"""remove debug app_config key

The `debug` setting only gated the API's interactive docs (/docs, /redoc,
/openapi.json) and was read once at startup, so editing it live did nothing
until a restart. Docs are now disabled unconditionally, so the key is removed.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM app_config WHERE key = 'debug'"))


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO app_config (key, value) VALUES ('debug', 'false') "
            "ON CONFLICT (key) DO NOTHING"
        )
    )
