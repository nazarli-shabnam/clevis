"""enforce case-insensitive uniqueness on users.email

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    collisions = conn.execute(
        sa.text(
            "SELECT lower(email) FROM users GROUP BY lower(email) HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if collisions:
        raise RuntimeError(
            "Cannot enforce case-insensitive email uniqueness: "
            f"{len(collisions)} email address(es) are shared by multiple existing users, "
            "differing only by case. Query \"SELECT lower(email) FROM users GROUP BY "
            "lower(email) HAVING COUNT(*) > 1\" to find them, then resolve these duplicate "
            "accounts manually (merge or rename one) before re-running this migration."
        )
    conn.execute(sa.text("UPDATE users SET email = lower(email) WHERE email <> lower(email)"))
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
    op.create_unique_constraint("users_email_key", "users", ["email"])
