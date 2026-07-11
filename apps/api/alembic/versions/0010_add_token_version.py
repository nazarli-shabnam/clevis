"""add users.token_version for JWT revocation

Sessions are stateless 30-day JWTs with no server-side revocation: demoting a workspace
admin, or a user wanting to invalidate their existing sessions, previously had zero effect
until natural token expiry. token_version is embedded in each issued JWT's claims and
compared against the user's current value on every authenticated request; bumping it
(via POST /auth/me/revoke-sessions) invalidates all previously issued tokens for that user.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
