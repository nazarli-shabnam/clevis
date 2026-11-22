"""SECURITY DEFINER detach of a deleted account's audit rows from its personal tenant.

Deleting an account removes its personal tenant, and ``audit_logs.tenant_id`` is a foreign key to it, so
the audit rows stored there (sign-in history, actions taken with a pasted token outside any org it belongs
to) either block the delete or, as the generic per-table cleanup did until now, get deleted with it. The
audit trail is append-only, so the rows must outlive the account.

``audit_logs.tenant_id`` is nullable (pre-tenant rows already have none) and the workspace-admin view reads
through ``audit_logs_for_admin*`` (0068/0075), which return every row regardless of tenant. Setting the
column to NULL therefore keeps the rows visible to the instance admin and out of every tenant's own view.
``audit_logs`` carries a strict RLS policy whose WITH CHECK is the same equality as its USING, so
``clevis_api`` cannot write NULL itself; this narrow function does it instead (pattern of 0069). It
changes only ``tenant_id``, only for a *personal* tenant, and only when the caller's session tenant is that
tenant, so it cannot be pointed at another tenant's or an org's rows. REVOKEd from PUBLIC, granted only to
``clevis_api``.

Additive: a new function, no table or data change. Rolling back the code leaves it unused.

Revision ID: 0093
Revises: 0092
Create Date: 2026-11-22
"""

from alembic import op

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None

_FN = "detach_tenant_audit_logs(integer)"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION detach_tenant_audit_logs(p_tenant_id integer)
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::int THEN
                RAISE EXCEPTION 'detach_tenant_audit_logs: session tenant does not match %', p_tenant_id
                    USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = p_tenant_id AND kind = 'personal') THEN
                RAISE EXCEPTION 'detach_tenant_audit_logs: % is not a personal tenant', p_tenant_id
                    USING ERRCODE = '42501';
            END IF;
            UPDATE audit_logs SET tenant_id = NULL WHERE tenant_id = p_tenant_id;
        END
        $$;
        """
    )
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_FN} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT EXECUTE ON FUNCTION {_FN} TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {_FN}")
