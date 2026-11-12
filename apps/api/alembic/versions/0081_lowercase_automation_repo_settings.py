"""Lower-case automation_repo_settings.repo and merge rows that differed only by case.

GitHub repo names are case-insensitive, but the primary key ``(tenant_id, repo, feature)`` compares
case-sensitively, so the same repository could hold two rows (``Acme/Api`` enabled, ``acme/api``
disabled). The run path collapsed them by whichever sorted last, so a repo an admin had disabled could
keep being auto-merged. The repository layer now stores and looks up the lower-cased key; this migration
brings existing rows in line.

Data change, not reversible: for each ``(tenant_id, lower(repo), feature)`` group the most recently
updated row is kept (ties: the lexicographically greater spelling) and the older ones are deleted, then
every remaining ``repo`` is lower-cased. Downgrade is a no-op: the merged-away rows and the original
casing cannot be reconstructed, and lower-case keys are valid under the old schema too.

Revision ID: 0081
Revises: 0080
Create Date: 2026-11-11
"""

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM automation_repo_settings a
        USING automation_repo_settings b
        WHERE a.tenant_id = b.tenant_id
          AND a.feature = b.feature
          AND lower(a.repo) = lower(b.repo)
          AND a.repo <> b.repo
          AND (a.updated_at < b.updated_at OR (a.updated_at = b.updated_at AND a.repo < b.repo))
        """
    )
    op.execute("UPDATE automation_repo_settings SET repo = lower(repo) WHERE repo <> lower(repo)")


def downgrade() -> None:
    pass
