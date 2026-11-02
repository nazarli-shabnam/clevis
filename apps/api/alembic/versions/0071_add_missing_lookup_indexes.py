"""Add the indexes that the per-tenant and per-installation lookups were missing.

Five query shapes had no usable index and scan more of their table as it grows:

* ``audit_logs (tenant_id, action, created_at)``: the digest sweep runs
  ``SELECT MAX(created_at) ... WHERE tenant_id = :t AND action = 'digest.sent'`` per tenant per
  tick, and ``audit_repo.has_any_action`` filters the same two columns. Only
  ``(tenant_id, id)`` existed.
* ``repo_events (tenant_id, occurred_at)``: the activity feed reads a tenant's newest events.
* ``repo_events (tenant_id, event_type, actor, occurred_at)``: the inactive-members query runs
  ``DISTINCT ON (actor) ... WHERE tenant_id = :t AND event_type = 'push' AND actor = ANY(...)``
  ordered by ``actor, occurred_at DESC``. Only ``(tenant_id)`` existed.
* ``github_installations (installation_id)``: webhook handling, permission refresh and uninstall
  look a row up by GitHub's installation id alone.
* ``orgs (lower(github_login))``: ``org_repo.get_by_login_ci`` filters ``lower(github_login)`` on
  every org-scoped request; the unique index on the plain column cannot serve that expression.
* ``scan_results (lower(owner), created_at DESC)``: owner-scoped scan reads compare
  ``lower(owner)``; the existing ``(owner, created_at)`` index cannot serve that expression.
  (``(tenant_id, created_at DESC)`` already exists and is not repeated here.)

Data risk: none. This only creates indexes; no rows, columns or constraints change. None of the
indexes is UNIQUE on purpose: existing data may hold duplicate installation ids or case-variant org
logins, and a failed unique build would stop the API from starting. A plain ``CREATE INDEX`` blocks
writes to its table while it builds. These tables are the largest in the schema (``audit_logs`` and
``repo_events`` grow with activity), so on a big instance expect a short write pause during the
migration, which the API entrypoint runs before it starts serving. ``CREATE INDEX CONCURRENTLY``
would avoid the pause but cannot run inside Alembic's migration transaction here (``env.py`` runs
every pending migration in one transaction), so it is deliberately not used.

Downgrade drops the six indexes. ``ix_repo_events_tenant_id`` is left in place: it is now a prefix
of both new ``repo_events`` indexes and could be dropped later, but removing an index that is
already in production is a separate decision from adding the missing ones.

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_tenant_id_action_created_at", "audit_logs", ["tenant_id", "action", "created_at"]
    )
    op.create_index("ix_repo_events_tenant_id_occurred_at", "repo_events", ["tenant_id", "occurred_at"])
    op.create_index(
        "ix_repo_events_tenant_id_event_type_actor_occurred_at",
        "repo_events",
        ["tenant_id", "event_type", "actor", "occurred_at"],
    )
    op.create_index("ix_github_installations_installation_id", "github_installations", ["installation_id"])
    op.create_index("ix_orgs_github_login_lower", "orgs", [sa.text("lower(github_login)")])
    op.create_index(
        "ix_scan_results_lower_owner_created_at",
        "scan_results",
        [sa.text("lower(owner)"), sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_scan_results_lower_owner_created_at", table_name="scan_results")
    op.drop_index("ix_orgs_github_login_lower", table_name="orgs")
    op.drop_index("ix_github_installations_installation_id", table_name="github_installations")
    op.drop_index("ix_repo_events_tenant_id_event_type_actor_occurred_at", table_name="repo_events")
    op.drop_index("ix_repo_events_tenant_id_occurred_at", table_name="repo_events")
    op.drop_index("ix_audit_logs_tenant_id_action_created_at", table_name="audit_logs")
