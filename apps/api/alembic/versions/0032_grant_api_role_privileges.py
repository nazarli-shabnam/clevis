"""grant clevis_api role privileges on all API-owned tables

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-14
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_TABLES = [
    "users",
    "orgs",
    "org_memberships",
    "tenants",
    "memberships",
    "invitations",
    "github_installations",
    "saved_tokens",
    "audit_logs",
    "scan_results",
    "jobs",
    "app_config",
]

# All _TABLES except app_config, whose primary key is a text `key` column with no
# backing sequence.
_SEQUENCE_TABLES = [
    "users",
    "orgs",
    "org_memberships",
    "tenants",
    "memberships",
    "invitations",
    "github_installations",
    "saved_tokens",
    "audit_logs",
    "scan_results",
    "jobs",
]


def upgrade() -> None:
    table_grants = "\n".join(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO clevis_api;" for table in _TABLES
    )
    sequence_grants = "\n".join(
        f"GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO clevis_api;" for table in _SEQUENCE_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT USAGE ON SCHEMA public TO clevis_api;
                {table_grants}
                {sequence_grants}
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    table_revokes = "\n".join(
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM clevis_api;" for table in _TABLES
    )
    sequence_revokes = "\n".join(
        f"REVOKE USAGE, SELECT ON SEQUENCE {table}_id_seq FROM clevis_api;" for table in _SEQUENCE_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                {sequence_revokes}
                {table_revokes}
                REVOKE USAGE ON SCHEMA public FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
