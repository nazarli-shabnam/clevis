"""Make repo_events' dedupe key per tenant: unique on (tenant_id, delivery_id).

0036 made delivery_id unique on its own. Backfilled rows are keyed "backfill:<GitHub event id>" and
GitHub event ids are global, so one real event appearing in two tenants' feeds (a member's push to
an org repo shows up in both their personal /users/{login}/events and the org's /orgs/{org}/events)
collided: whichever tenant backfilled second hit ON CONFLICT DO NOTHING and silently got no
repo_events row and no daily-count rollup. Scoping the key to the tenant fixes that.

Safe on existing data: every row already satisfies the narrower (tenant_id, delivery_id) key, so the
new constraint cannot fail, and nothing is rewritten or deleted. The new constraint is created
before the old one is dropped so dedupe is never absent. Both statements take a brief ACCESS
EXCLUSIVE lock on repo_events (an index build); the table is bounded by the retention sweep.

Downgrade re-creates the single-column key and FAILS if two tenants now hold the same delivery_id
(which is exactly the data this migration allows). Resolve those duplicates deliberately before
downgrading; nothing is deleted automatically.

Revision ID: 0074
Revises: 0073
Create Date: 2026-11-04
"""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both statements take an ACCESS EXCLUSIVE lock on repo_events. Fail fast instead of queueing behind a long
    # transaction (which would stall every webhook insert and feed read queued after us); just re-run the migration.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_unique_constraint("uq_repo_events_tenant_delivery", "repo_events", ["tenant_id", "delivery_id"])
    op.drop_constraint("uq_repo_events_delivery_id", "repo_events", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("uq_repo_events_delivery_id", "repo_events", ["delivery_id"])
    op.drop_constraint("uq_repo_events_tenant_delivery", "repo_events", type_="unique")
