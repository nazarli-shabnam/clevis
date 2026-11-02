"""422 responses must not echo the request body, and GitHub-token body fields are SecretStr."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rate_limit import _buckets as _rate_limit_buckets
from src.main import app
from src.routers.branch_protection import BulkRequest
from src.routers.dependabot_triage import TriageRequest
from src.routers.issues import CreateIssueRequest
from src.routers.pr_nudges import NudgeRequest
from src.routers.remediation import RemediateRequest
from src.routers.remediation_bulk import BulkRemediateRequest
from src.routers.workflow_lint import LintRequest
from src.services.token_resolution import pick_client_token

_USER = UserOut(id=1, email="u@example.com", name=None, is_workspace_admin=False)
_TOKEN = "ghp_sEcReTtOkEn1234567890"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # /auth/register is rate limited per IP; start from an empty bucket so the 422 is what we see.
    _rate_limit_buckets.clear()
    yield
    _rate_limit_buckets.clear()


@pytest.fixture()
def client(db):
    app.dependency_overrides[require_auth] = lambda: _USER
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_auth, None)
        app.dependency_overrides.pop(get_db, None)


def test_missing_field_422_does_not_echo_the_github_token(client):
    # `title` is required, so validation fails with the whole body as the error input.
    resp = client.post("/me/repos/acme/api/issues", json={"token": _TOKEN, "body": "details"})

    assert resp.status_code == 422
    assert _TOKEN not in resp.text
    assert all("input" not in error for error in resp.json()["detail"])


def test_invalid_type_422_does_not_echo_the_github_token(client):
    resp = client.post("/me/repos/acme/api/issues", json={"token": _TOKEN, "title": ["not", "a", "string"]})

    assert resp.status_code == 422
    assert _TOKEN not in resp.text


def test_422_keeps_the_location_and_message_clients_display(client):
    resp = client.post("/me/repos/acme/api/issues", json={"token": _TOKEN})

    detail = resp.json()["detail"]
    assert detail and all("loc" in error and "msg" in error and "type" in error for error in detail)
    assert detail[0]["loc"] == ["body", "title"]
    assert "input" not in detail[0]


def test_422_does_not_echo_a_password_either(client):
    resp = client.post("/auth/register", json={"email": "not-an-email", "password": "hunter2-correct-horse"})

    assert resp.status_code == 422
    assert "hunter2-correct-horse" not in resp.text


@pytest.mark.parametrize(
    "model, extra",
    [
        (RemediateRequest, {}),
        (CreateIssueRequest, {"title": "t"}),
        (BulkRequest, {"repos": ["a"]}),
        (TriageRequest, {}),
        (NudgeRequest, {}),
        (LintRequest, {}),
        (BulkRemediateRequest, {"check_id": "x", "repos": ["a"]}),
    ],
)
def test_github_token_body_fields_are_secret_str(model, extra):
    parsed = model(token=_TOKEN, **extra)
    assert isinstance(parsed.token, SecretStr)
    assert _TOKEN not in repr(parsed)
    assert _TOKEN not in str(parsed.model_dump())


def test_pick_client_token_prefers_the_body_then_the_header():
    assert pick_client_token(SecretStr("body"), "header") == "body"
    assert pick_client_token(None, "header") == "header"
    assert pick_client_token(SecretStr(""), "header") == "header"
    assert pick_client_token(None, None) is None
