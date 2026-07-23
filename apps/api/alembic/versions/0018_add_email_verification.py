"""add users.email_verified/email_verify_token/email_verify_token_expires_at

Issue #217: POST /auth/register creates an account with no email-ownership
verification, and POST /invitations/{token}/accept grants org membership on a
plain email string match -- so an attacker who knows a victim's email can
register with it first, then accept an invite meant for that email under the
victim's identity. The GitHub OAuth path already defends against the
equivalent attack (EmailAlreadyRegistered refuses to auto-link on email
match, since GitHub vouches for the email); the password path had no
equivalent because there was nothing on the User model to represent "this
email has been proven."

- email_verified: bool, NOT NULL. Existing rows are backfilled true via
  server_default=sa.true() -- every user who already has an account today is
  grandfathered in as trusted (this migration doesn't retroactively lock
  anyone out); going forward, application code explicitly sets it per user
  (True for GitHub-linked/first-run-setup users, False for self-registered
  users) rather than relying on the server default for new inserts.
- email_verify_token / email_verify_token_expires_at: nullable, no backfill
  needed -- only set when a verification email is pending.

Additive-only, no data-loss risk. Same style as 0016_add_scan_results_scanned_by_user_id.py.

Renumbered from 0017 to 0018 (originally authored against down_revision 0016, before
0017_add_jobs_heartbeat_at.py -- issue #215 -- merged to main first and claimed "0017").

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("users", sa.Column("email_verify_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email_verify_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_email_verify_token", "users", ["email_verify_token"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email_verify_token", "users", type_="unique")
    op.drop_column("users", "email_verify_token_expires_at")
    op.drop_column("users", "email_verify_token")
    op.drop_column("users", "email_verified")
