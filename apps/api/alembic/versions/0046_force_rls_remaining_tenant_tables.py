"""Add FORCE ROW LEVEL SECURITY to the S3-S6 tenant tables (issue #412).

Migrations 0036, 0037, 0038, 0039, 0040, 0041, 0043 each ENABLE ROW LEVEL SECURITY on
their new table but never add FORCE -- so, same gap 0031's docstring already described
for memberships/github_installations/scan_results before it added FORCE there, RLS was
inert for these tables' owner (the API's own DB role) the whole time, S3 through S6.

Unlike 0031, this migration needs no policy widening first. 0031's audit found writes to
memberships/github_installations that never went through require_org_role/
require_personal_tenant (so app.tenant_id was never set to the row's own tenant) and
needed a self-access USING/WITH CHECK carve-out before FORCE was safe. A matching audit
for these 8 tables (see issue #412's PR description) found no such gap:

  - repo_events, repo_event_daily_counts, activity_sync_cursors (worker.py's backfill
    handler, apps/worker/src/repo_events_store.py) and security_alerts
    (apps/worker/src/event_consumer.py's security-alert branch,
    apps/worker/src/security_alerts_store.py) are all written from apps/worker after an
    explicit `SET app.tenant_id = <n>` on the same cursor, matching the tenant_id of
    whatever's about to be inserted/updated.
  - org_members, repo_collaborators, org_membership_sync_cursors
    (apps/worker/src/org_membership_store.py, called from both event_consumer.py's
    member/organization branch and worker.py's membership-reconcile handler) are the
    same: the caller sets app.tenant_id on the cursor immediately before calling in.
  - automation_repo_settings (apps/api/src/routers/dependabot_triage.py,
    branch_protection.py) is written only from request handlers gated by
    require_org_role, which already calls set_tenant_session_context before the handler
    body runs.

All eight have a plain tenant_id-equality policy with an implicit WITH CHECK matching
USING (Group A per migration 0030's docstring -- tenant_id is NOT NULL on every one of
them, no OR-NULL carve-out to preserve), so FORCE is a direct, no-risk addition here.

*** Same caveat as 0031: in the default docker-compose deployment, DB_USER is a Postgres
superuser (the initdb bootstrap role), and superusers unconditionally bypass RLS
regardless of ENABLE/FORCE. This migration's FORCE flags are correct and inert-but-
harmless until an operator opts into the non-superuser clevis_api/clevis_worker roles
(API_DB_PASSWORD/WORKER_DB_PASSWORD -- see docs/self-hosting.md), at which point they
start enforcing with no further schema change needed. Deliberately staying opt-in for
this PR, not flipped to a new default -- see docs/self-hosting.md's updated note. ***

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_FORCE_TABLES = [
    "repo_events",
    "repo_event_daily_counts",
    "activity_sync_cursors",
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "org_membership_sync_cursors",
    "automation_repo_settings",
]


def upgrade() -> None:
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
