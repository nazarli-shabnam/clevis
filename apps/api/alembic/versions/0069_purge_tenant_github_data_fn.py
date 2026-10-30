"""SECURITY DEFINER purge of a tenant's GitHub-derived data after the last installation is removed.

The uninstall purge (installation_repo.purge_tenant_github_data_if_disconnected) ran
`DELETE FROM <table> WHERE tenant_id = ...` as the API role, but clevis_api holds no DELETE on
repo_events, repo_event_daily_counts, activity_sync_cursors, security_alerts or
org_membership_sync_cursors (0036-0041), and the resulting ProgrammingError was swallowed as a log
warning. Events, alerts, rollups and sync cursors therefore survived an uninstall and resurfaced on
reinstall.

Granting DELETE on five more tables would widen clevis_api's standing access, so the purge goes
through one narrow SECURITY DEFINER function instead (pattern of 0035/0047/0068). It refuses unless
the caller's session tenant is the tenant being purged and that tenant has no installation left, so
it cannot be pointed at another tenant's data. REVOKEd from PUBLIC, granted only to clevis_api.

Trust boundary: "the caller's session tenant" is the `app.tenant_id` setting, which clevis_api can
set itself, so the guard stops a bug in trusted API code from purging the wrong tenant; it does not
stop a compromised clevis_api session, which could equally set any tenant context and read that
tenant's rows. The same holds for every tenant-scoped RLS policy in this schema.

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-30
"""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

_TABLES = (
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "activity_sync_cursors",
    "org_membership_sync_cursors",
    "automation_repo_settings",
    "repo_event_daily_counts",
    "repo_events",
)


def upgrade() -> None:
    deletes = "\n".join(f"    DELETE FROM {t} WHERE tenant_id = p_tenant_id;" for t in _TABLES)
    op.execute(
        f"""
        CREATE FUNCTION purge_tenant_github_data(p_tenant_id integer)
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::int THEN
                RAISE EXCEPTION 'purge_tenant_github_data: session tenant does not match %', p_tenant_id
                    USING ERRCODE = '42501';
            END IF;
            IF EXISTS (SELECT 1 FROM github_installations WHERE tenant_id = p_tenant_id) THEN
                RETURN;
            END IF;
{deletes}
        END
        $$;
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION purge_tenant_github_data(integer) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT EXECUTE ON FUNCTION purge_tenant_github_data(integer) TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS purge_tenant_github_data(integer)")
