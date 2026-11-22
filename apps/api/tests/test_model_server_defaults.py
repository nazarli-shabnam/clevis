"""The models carry the server defaults the database must have (guarded end to end by `alembic check`, which
runs with compare_server_default in CI). Raw INSERTs that omit these columns must not fail open (#1231)."""

import pytest

from src.core.db import Base


@pytest.mark.parametrize(
    "table,column,expected",
    [
        ("jobs", "status", "'queued'"),
        ("jobs", "retry_count", "0"),
        ("users", "email_verified", "false"),
        ("users", "is_workspace_admin", "false"),
        ("users", "token_version", "0"),
        ("invitations", "status", "'pending'"),
        ("webhook_deliveries", "status", "'queued'"),
    ],
)
def test_model_declares_the_expected_server_default(table, column, expected):
    default = Base.metadata.tables[table].c[column].server_default
    assert default is not None and str(default.arg) == expected
