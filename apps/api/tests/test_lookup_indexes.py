"""Migration 0071: the lookup indexes exist, and the queries they were added for can use them."""

import pytest
from sqlalchemy import text

_EXPECTED = {
    "ix_audit_logs_tenant_id_action_created_at": ("audit_logs", "(tenant_id, action, created_at)"),
    "ix_repo_events_tenant_id_occurred_at": ("repo_events", "(tenant_id, occurred_at)"),
    "ix_repo_events_tenant_id_event_type_actor_occurred_at": (
        "repo_events",
        "(tenant_id, event_type, actor, occurred_at)",
    ),
    "ix_github_installations_installation_id": ("github_installations", "(installation_id)"),
    "ix_orgs_github_login_lower": ("orgs", "(lower(github_login))"),
    "ix_scan_results_lower_owner_created_at": ("scan_results", "(lower((owner)::text), created_at DESC)"),
}


def test_the_indexes_exist_on_the_expected_columns(db):
    rows = db.execute(
        text("SELECT indexname, tablename, indexdef FROM pg_indexes WHERE indexname = ANY(:names)"),
        {"names": list(_EXPECTED)},
    ).fetchall()
    found = {row.indexname: row for row in rows}

    assert set(found) == set(_EXPECTED)
    for name, (table, columns) in _EXPECTED.items():
        assert found[name].tablename == table
        assert columns in found[name].indexdef, found[name].indexdef


def test_the_indexes_are_not_unique(db):
    """A unique build could fail on existing duplicates and stop the API from starting."""
    unique = db.execute(
        text("SELECT indexname FROM pg_indexes WHERE indexname = ANY(:names) AND indexdef ILIKE 'CREATE UNIQUE%'"),
        {"names": list(_EXPECTED)},
    ).fetchall()

    assert unique == []


def _plan(db, sql: str) -> str:
    # The tables are tiny in tests, so the planner would pick a sequential scan whatever the indexes are.
    # Disabling it shows whether an index *can* serve the query; SET LOCAL ends with the test's savepoint.
    db.execute(text("SET LOCAL enable_seqscan = off"))
    return "\n".join(row[0] for row in db.execute(text("EXPLAIN " + sql)).fetchall())


@pytest.mark.parametrize(
    "sql, index",
    [
        # org_repo.get_by_login_ci
        ("SELECT * FROM orgs WHERE lower(github_login) = 'acme'", "ix_orgs_github_login_lower"),
        # installation_repo lookups by GitHub's installation id
        (
            "SELECT * FROM github_installations WHERE installation_id = 7",
            "ix_github_installations_installation_id",
        ),
        # scan_results_repo's owner-scoped latest-scan read
        (
            "SELECT * FROM scan_results WHERE lower(owner) = 'acme' ORDER BY created_at DESC LIMIT 1",
            "ix_scan_results_lower_owner_created_at",
        ),
        # (The audit_logs digest query and the two repo_events queries are not listed: older indexes sharing their
        # tenant_id prefix can also serve them, so which one the planner picks depends on table statistics and a
        # plan assertion would be flaky. Their indexes are covered by the definition test above.)
    ],
)
def test_the_query_can_use_its_index(db, sql, index):
    assert index in _plan(db, sql)
