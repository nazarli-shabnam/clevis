"""Add jobs.tenant_id so an org's admins can see their own background jobs.

``jobs`` has had no tenant link (the worker only needs the payload), so no per-org view of it was
possible. This adds a nullable ``tenant_id`` (FK to ``tenants``) plus an index on
``(tenant_id, id)`` for "this tenant's newest jobs", and backfills it where the attribution is
unambiguous:

* ``github.backfill_repo_events`` / ``github.reconcile_org_membership`` payloads carry
  ``tenant_id`` directly (used only if that tenant still exists);
* ``github.clear_actions_cache`` payloads carry ``owner``; it is attributed when that login matches
  exactly one org (case-insensitively). A cache clear for a *personal* repo can't be tied to a
  tenant from the payload alone, so it stays NULL.

Rows left NULL are simply not visible in any org's job list; nothing else reads the column.

Data risk: none. The column is added nullable with no default, the backfill only fills NULLs and
never rewrites or deletes anything, and it parses each payload defensively (a malformed one is
skipped, it cannot abort the migration). The index build and FK add take brief locks on ``jobs``,
which is small. Downgrade drops the FK, index and column (losing only the attribution).
``clevis_api`` and ``clevis_worker`` hold table-level privileges on ``jobs`` (0020/0032), which
cover the new column, so no grant changes are needed. RLS is deliberately not enabled on ``jobs``
(the worker reads and claims rows across tenants).

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-06
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None

_TENANT_IN_PAYLOAD = ("github.backfill_repo_events", "github.reconcile_org_membership")
_OWNER_IN_PAYLOAD = "github.clear_actions_cache"


def _backfill() -> None:
    conn = op.get_bind()
    tenant_ids = {row[0] for row in conn.execute(sa.text("SELECT id FROM tenants"))}

    # lower(login) -> tenant id, but only for logins that identify a single org.
    by_login: dict[str, int | None] = {}
    for login, tenant_id in conn.execute(sa.text("SELECT github_login, tenant_id FROM orgs WHERE tenant_id IS NOT NULL")):
        key = login.lower()
        by_login[key] = tenant_id if key not in by_login else None  # None marks an ambiguous login

    updates: list[dict] = []
    rows = conn.execute(
        sa.text("SELECT id, job_type, payload FROM jobs WHERE tenant_id IS NULL AND job_type = ANY(:types)").bindparams(
            types=[*_TENANT_IN_PAYLOAD, _OWNER_IN_PAYLOAD]
        )
    )
    for job_id, job_type, raw in rows:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        tenant_id: int | None = None
        if job_type in _TENANT_IN_PAYLOAD:
            value = payload.get("tenant_id")
            if isinstance(value, int) and not isinstance(value, bool) and value in tenant_ids:
                tenant_id = value
        else:
            owner = payload.get("owner")
            if isinstance(owner, str):
                tenant_id = by_login.get(owner.lower())
        if tenant_id is not None:
            updates.append({"job_id": job_id, "tenant_id": tenant_id})

    if updates:
        conn.execute(sa.text("UPDATE jobs SET tenant_id = :tenant_id WHERE id = :job_id AND tenant_id IS NULL"), updates)


def upgrade() -> None:
    op.add_column("jobs", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_jobs_tenant_id_tenants", "jobs", "tenants", ["tenant_id"], ["id"])
    op.create_index("ix_jobs_tenant_id_id", "jobs", ["tenant_id", "id"], unique=False)
    # The org activity log pages audit_logs by (tenant_id, id); it had no tenant index.
    op.create_index("ix_audit_logs_tenant_id_id", "audit_logs", ["tenant_id", "id"], unique=False)
    _backfill()


def downgrade() -> None:
    op.drop_index("ix_audit_logs_tenant_id_id", table_name="audit_logs")
    op.drop_index("ix_jobs_tenant_id_id", table_name="jobs")
    op.drop_constraint("fk_jobs_tenant_id_tenants", "jobs", type_="foreignkey")
    op.drop_column("jobs", "tenant_id")
