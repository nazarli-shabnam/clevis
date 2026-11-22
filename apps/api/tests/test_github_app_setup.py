"""Creating the GitHub App from Settings (manifest flow) and reading its credentials from the database."""

import contextlib
from itertools import count
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import AuditLog, GitHubAppCredentials, User, get_db
from src.routers.github_app_setup import router
from src.routers.webhooks import _INGESTED_EVENT_TYPES
from src.services import app_permissions, github_app, github_app_credentials, github_app_manifest
from src.services.github_app_manifest import ManifestError

UI = "http://localhost:3000"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
GITHUB_ANSWER = {
    "id": 4242, "slug": "clevis-acme", "client_id": "Iv1.abc", "client_secret": "cs", "webhook_secret": "ws", "pem": PEM,
}


@pytest.fixture(autouse=True)
def _no_env_app(monkeypatch):
    for name in ("github_app_id", "github_app_slug", "github_app_client_id"):
        monkeypatch.setattr(settings, name, None)
    for name in ("github_app_private_key", "github_app_client_secret", "github_app_webhook_secret"):
        monkeypatch.setattr(settings, name, None)
    github_app_credentials.clear_cache()
    yield
    github_app_credentials.clear_cache()
    github_app.clear_cache()


@pytest.fixture(autouse=True)
def _resolver_reads_the_test_session(db, monkeypatch):
    """The resolver opens its own session in production; the test's rows live in an uncommitted transaction that a
    second connection cannot see, so hand it the test session."""
    monkeypatch.setattr(github_app_credentials, "SessionLocal", lambda: contextlib.nullcontext(db))


_emails = count(1)


def _user(db, admin=True) -> UserOut:
    row = User(email=f"gha{next(_emails)}@example.com", name=None, password_hash=None, is_workspace_admin=admin)
    db.add(row)
    db.commit()
    db.refresh(row)
    return UserOut(id=row.id, email=row.email, name=None, is_workspace_admin=admin)


def _client(db, user) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(router)
    return TestClient(app, base_url="https://api.example.com")


def _manifest(client, **over):
    return client.post("/github-app/manifest", json={"ui_origin": UI, **over})


# --- the manifest itself (pure) ---------------------------------------------------------------------------------

def test_the_manifest_requests_everything_the_code_uses_and_every_url_comes_from_the_instance():
    m = github_app_manifest.build_manifest(ui_origin=UI, api_base="https://api.example.com/", name="Clevis", read_only=False)
    assert m["default_permissions"] == app_permissions.manifest_permissions(False)
    assert set(m["default_events"]) == _INGESTED_EVENT_TYPES
    assert m["hook_attributes"] == {"url": "https://api.example.com/webhooks/github", "active": True}
    assert m["callback_urls"] == ["https://api.example.com/auth/github/callback"]
    assert m["setup_url"] == f"{UI}/settings/github-callback" and m["setup_on_update"] is True
    assert m["redirect_url"] == f"{UI}/settings/github-app-callback"
    perms = m["default_permissions"]
    # the union of the baseline and every feature, at the highest level any of them needs
    assert perms["administration"] == "write" and perms["contents"] == "write" and perms["workflows"] == "write"
    assert perms["actions"] == "write" and perms["checks"] == "read" and perms["statuses"] == "read"
    assert perms["organization_administration"] == "read" and perms["metadata"] == "read"
    assert perms["emails"] == "read"  # sign-in falls back to GET /user/emails


def test_read_only_drops_every_write_permission():
    perms = app_permissions.manifest_permissions(True)
    assert "write" not in perms.values() and "workflows" not in perms
    assert set(app_permissions.BASELINE_PERMISSIONS) <= set(perms)
    assert perms["emails"] == "read"


def test_the_post_url_targets_the_user_or_the_organization_and_rejects_a_bad_org_name():
    assert github_app_manifest.post_url(None, "S").endswith("/settings/apps/new?state=S")
    assert "/organizations/acme/settings/apps/new?state=S" in github_app_manifest.post_url("acme", "S")
    with pytest.raises(ManifestError):
        github_app_manifest.post_url("acme/../x", "S")


def test_a_ui_origin_outside_cors_origins_is_refused():
    assert github_app_manifest.check_ui_origin(UI + "/") == UI
    for bad in ("https://evil.example", UI + "/path", "javascript:alert(1)"):
        with pytest.raises(ManifestError):
            github_app_manifest.check_ui_origin(bad)


def test_the_state_is_bound_to_the_admin_session_and_expires():
    state = github_app_manifest.sign_state(user_id=1, token_version=3, replace=False)
    assert github_app_manifest.verify_state(state, user_id=1, token_version=3)["uid"] == 1
    for kwargs in ({"user_id": 2, "token_version": 3}, {"user_id": 1, "token_version": 4}):
        with pytest.raises(ManifestError):
            github_app_manifest.verify_state(state, **kwargs)
    old = github_app_manifest.sign_state(user_id=1, token_version=3, replace=False, now=1)
    with pytest.raises(ManifestError):
        github_app_manifest.verify_state(old, user_id=1, token_version=3)
    with pytest.raises(ManifestError):
        github_app_manifest.verify_state("not-a-token", user_id=1, token_version=3)


def test_convert_code_validates_the_code_and_the_answer():
    with pytest.raises(ManifestError):
        github_app_manifest.convert_code("bad/code?x")

    def post(status, body):
        return patch("src.services.github_app_manifest.httpx.post", return_value=httpx.Response(status, json=body, request=httpx.Request("POST", "http://x")))

    with post(201, GITHUB_ANSWER):
        assert github_app_manifest.convert_code("abc123")["slug"] == "clevis-acme"
    with post(404, {}), pytest.raises(ManifestError):
        github_app_manifest.convert_code("abc123")
    with post(201, {**GITHUB_ANSWER, "pem": ""}), pytest.raises(ManifestError):
        github_app_manifest.convert_code("abc123")
    with patch("src.services.github_app_manifest.httpx.post", side_effect=httpx.ConnectError("down")), pytest.raises(ManifestError):
        github_app_manifest.convert_code("abc123")


# --- the endpoints ----------------------------------------------------------------------------------------------

def test_only_a_workspace_admin_may_use_it(db):
    member = _client(db, _user(db, admin=False))
    assert member.get("/github-app").status_code == 403
    assert _manifest(member).status_code == 403
    assert member.post("/github-app/manifest/convert", json={"code": "c", "state": "s"}).status_code == 403


def test_status_reports_the_requirements_and_where_the_app_comes_from(db):
    body = _client(db, _user(db)).get("/github-app").json()
    assert body["source"] == "none" and body["configured"] is False
    assert body["permissions"] == app_permissions.manifest_permissions(False)
    assert body["read_only_permissions"] == app_permissions.manifest_permissions(True)
    assert body["events"] == sorted(_INGESTED_EVENT_TYPES)


def test_status_lists_what_the_registered_app_itself_lacks(db, monkeypatch):
    monkeypatch.setattr(github_app, "credentials_configured", lambda: True)
    read_only = app_permissions.manifest_permissions(True)
    monkeypatch.setattr(
        github_app, "get_app", lambda: {"permissions": read_only, "owner": {"login": "acme", "type": "Organization"}}
    )
    body = _client(db, _user(db)).get("/github-app").json()
    missing = body["app_missing_permissions"]
    assert missing["issues"] == "write" and missing["workflows"] == "write" and missing["administration"] == "write"
    assert "emails" not in missing  # a user permission, never an installation one
    assert (body["app_owner_login"], body["app_owner_type"]) == ("acme", "Organization")
    assert body["app_check_error"] is None


def test_status_reports_no_gap_when_the_app_requests_everything(db, monkeypatch):
    monkeypatch.setattr(github_app, "credentials_configured", lambda: True)
    monkeypatch.setattr(github_app, "get_app", lambda: {"permissions": app_permissions.manifest_permissions(False)})
    assert _client(db, _user(db)).get("/github-app").json()["app_missing_permissions"] == {}


def test_status_takes_the_slug_from_github_when_the_credentials_have_none(db, monkeypatch):
    monkeypatch.setattr(github_app, "credentials_configured", lambda: True)
    monkeypatch.setattr(github_app_credentials, "slug", lambda: None)
    monkeypatch.setattr(github_app, "get_app", lambda: {"slug": "clevis-prod", "permissions": {}})
    assert _client(db, _user(db)).get("/github-app").json()["slug"] == "clevis-prod"


def test_status_flags_an_answer_without_permissions(db, monkeypatch):
    monkeypatch.setattr(github_app, "credentials_configured", lambda: True)
    monkeypatch.setattr(github_app, "get_app", lambda: {})
    body = _client(db, _user(db)).get("/github-app").json()
    assert body["app_missing_permissions"] is None and body["app_check_error"]


def test_status_still_answers_when_github_cannot_be_asked(db, monkeypatch):
    monkeypatch.setattr(github_app, "credentials_configured", lambda: True)

    def down():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(github_app, "get_app", down)
    resp = _client(db, _user(db)).get("/github-app")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app_missing_permissions"] is None and body["app_check_error"]


def test_the_manifest_endpoint_builds_the_manifest_from_the_request_origin(db):
    resp = _manifest(_client(db, _user(db)), organization="acme", read_only=True, name="My Clevis")
    assert resp.status_code == 200
    body = resp.json()
    assert body["manifest"]["name"] == "My Clevis"
    assert body["manifest"]["hook_attributes"]["url"] == "https://api.example.com/webhooks/github"
    assert "write" not in body["manifest"]["default_permissions"].values()
    assert body["post_url"].startswith("https://github.com/organizations/acme/settings/apps/new?state=")
    assert _manifest(_client(db, _user(db)), ui_origin="https://evil.example").status_code == 422


def test_environment_credentials_block_creating_another_app(db, monkeypatch):
    monkeypatch.setattr(settings, "github_app_id", "1")
    client = _client(db, _user(db))
    assert _manifest(client).status_code == 409
    assert client.post("/github-app/manifest/convert", json={"code": "c", "state": "s"}).status_code == 409
    assert client.get("/github-app").json()["source"] == "env"


def _convert(client, user_state, code="abc123"):
    with patch(
        "src.services.github_app_manifest.httpx.post",
        return_value=httpx.Response(201, json=GITHUB_ANSWER, request=httpx.Request("POST", "http://x")),
    ):
        return client.post("/github-app/manifest/convert", json={"code": code, "state": user_state})


def test_converting_stores_the_credentials_encrypted_and_the_app_is_used_without_a_restart(db):
    user = _user(db)
    client = _client(db, user)
    state = _manifest(client).json()["state"]

    resp = _convert(client, state)

    assert resp.status_code == 200 and resp.json() == {"slug": "clevis-acme", "app_id": "4242"}
    row = db.get(GitHubAppCredentials, 1)
    assert row.app_id == "4242" and row.created_by_user_id == user.id
    # nothing sensitive is stored in the clear
    assert PEM not in row.private_key_enc and "cs" != row.client_secret_enc and "ws" != row.webhook_secret_enc
    # and the readers see it straight away
    assert github_app_credentials.app_id() == "4242"
    assert github_app_credentials.private_key() == PEM
    assert github_app_credentials.client_secret() == "cs" and github_app_credentials.webhook_secret() == "ws"
    assert github_app_credentials.slug() == "clevis-acme" and github_app.credentials_configured() is True
    assert github_app_credentials.source() == "database"
    assert db.query(AuditLog).filter(AuditLog.action == "github_app.created").count() == 1


def test_the_environment_wins_field_by_field_over_the_stored_app(db, monkeypatch):
    client = _client(db, _user(db))
    _convert(client, _manifest(client).json()["state"])
    monkeypatch.setattr(settings, "github_app_webhook_secret", SecretStr("from-env"))
    github_app_credentials.clear_cache()
    assert github_app_credentials.webhook_secret() == "from-env"
    assert github_app_credentials.client_secret() == "cs"  # not set in the environment: the stored one


def test_a_state_from_another_admin_or_a_replay_without_confirmation_is_refused(db):
    first = _user(db)
    state = _manifest(_client(db, first)).json()["state"]
    other = User(email=f"gha{next(_emails)}@example.com", name=None, password_hash=None, is_workspace_admin=True)
    db.add(other)
    db.commit()
    db.refresh(other)
    other_out = UserOut(id=other.id, email=other.email, name=None, is_workspace_admin=True)
    assert _convert(_client(db, other_out), state).status_code == 422

    client = _client(db, first)
    assert _convert(client, state).status_code == 200
    # An App now exists: a second creation needs an explicit replace, in the request and in the signed state.
    assert _manifest(client).status_code == 409
    assert _convert(client, state).status_code == 422
    replace_state = _manifest(client, replace=True).json()["state"]
    assert _convert(client, replace_state).status_code == 200


def test_the_replacement_check_reads_the_table_not_a_stale_credentials_cache(db, monkeypatch):
    """Two overlapping first-time conversions: the loser's cached "no App yet" must not let it overwrite the winner."""
    user = _user(db)
    client = _client(db, user)
    state = _manifest(client).json()["state"]
    assert _convert(client, state).status_code == 200
    monkeypatch.setattr(github_app_credentials, "stored", lambda: None)  # what a cache filled before the winner saw
    second_state = github_app_manifest.sign_state(user_id=user.id, token_version=0, replace=False)
    assert _convert(client, second_state).status_code == 422


def test_a_refused_code_stores_nothing(db):
    client = _client(db, _user(db))
    state = _manifest(client).json()["state"]
    with patch(
        "src.services.github_app_manifest.httpx.post",
        return_value=httpx.Response(404, json={}, request=httpx.Request("POST", "http://x")),
    ):
        assert client.post("/github-app/manifest/convert", json={"code": "abc", "state": state}).status_code == 422
    assert db.get(GitHubAppCredentials, 1) is None
