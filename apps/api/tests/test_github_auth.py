"""Tests for the GitHub OAuth router + find-or-create (DB-backed)."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sqlalchemy.orm import Query

from src.core.config import settings
from src.core.db import User, get_db
from src.core.rate_limit import _buckets as _rate_limit_buckets
from src.repositories import tenant_repo
from src.routers.github_auth import EmailAlreadyRegistered, RegistrationDisabled, find_or_create_user
from src.routers.github_auth import router as gh_router
from src.services import github_oauth
from src.services.github_oauth import GitHubIdentity


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """/auth/github/callback is rate-limited per client IP via a module-level in-memory
    bucket that persists across tests in the same process -- reset it so one test's request
    volume can't tip a later, unrelated test over the threshold (same pattern as test_auth.py)."""
    _rate_limit_buckets.clear()
    yield
    _rate_limit_buckets.clear()


@pytest.fixture()
def gh_app(db):
    a = FastAPI()
    a.include_router(gh_router, prefix="/auth/github")
    a.dependency_overrides[get_db] = lambda: db
    return a


@pytest.fixture()
def gh_client(gh_app):
    # https base_url so Secure-flagged cookies (session + OAuth state, both set with
    # secure=settings.session_cookie_secure which defaults True) actually round-trip
    # through the client's cookie jar across requests, matching real browser behavior.
    return TestClient(gh_app, base_url="https://testserver")


@pytest.fixture()
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_app_client_id", "Iv1.abc")
    monkeypatch.setattr(settings, "github_app_client_secret", SecretStr("secret"))


def test_github_signup_is_allowed_when_registration_is_disabled_but_the_email_has_a_pending_invitation(db):
    from src.repositories import invitation_repo, org_repo

    owner = find_or_create_user(db, _identity())
    org = org_repo.get_or_create(db, github_login="acme")
    invitation_repo.create(db, org_id=org.id, email="hubot@example.com", invited_by_user_id=owner.id)

    with patch("src.routers.github_auth.registration_enabled", return_value=False):
        invited = find_or_create_user(db, _identity(github_user_id=2002, login="hubot", email="hubot@example.com"))
        with pytest.raises(RegistrationDisabled):
            find_or_create_user(db, _identity(github_user_id=2003, login="other", email="other@example.com"))
    assert invited.email == "hubot@example.com" and invited.email_verified is True


def _identity(**kw) -> GitHubIdentity:
    base = dict(github_user_id=1001, login="octocat", name="Octo", email="octo@example.com", avatar_url=None)
    base.update(kw)
    return GitHubIdentity(**base)


def test_first_user_becomes_owner(db):
    user = find_or_create_user(db, _identity())
    assert user.is_workspace_admin is True
    assert user.github_user_id == 1001
    assert user.password_hash is None


def test_second_user_is_member(db):
    find_or_create_user(db, _identity())
    second = find_or_create_user(db, _identity(github_user_id=2002, login="hubot", email="hubot@example.com"))
    assert second.is_workspace_admin is False


def test_new_github_signup_rejected_when_registration_disabled(db):
    find_or_create_user(db, _identity())
    with patch("src.routers.github_auth.registration_enabled", return_value=False):
        with pytest.raises(RegistrationDisabled):
            find_or_create_user(db, _identity(github_user_id=2002, login="hubot", email="hubot@example.com"))
    assert db.query(User).filter(User.github_user_id == 2002).first() is None


def test_first_github_signup_allowed_even_when_registration_disabled(db):
    with patch("src.routers.github_auth.registration_enabled", return_value=False):
        assert find_or_create_user(db, _identity()).is_workspace_admin is True


def test_returning_github_user_unaffected_by_registration_disabled(db):
    find_or_create_user(db, _identity())
    with patch("src.routers.github_auth.registration_enabled", return_value=False):
        assert find_or_create_user(db, _identity()).github_user_id == 1001


def test_new_user_gets_a_personal_tenant_and_self_membership(db):
    from src.core.db import Membership, Tenant

    user = find_or_create_user(db, _identity())

    tenant = db.query(Tenant).filter(Tenant.kind == "personal", Tenant.personal_user_id == user.id).first()
    assert tenant is not None
    membership = (
        db.query(Membership).filter(Membership.tenant_id == tenant.id, Membership.user_id == user.id).first()
    )
    assert membership is not None
    assert membership.role == "admin"


def test_returning_user_does_not_duplicate_the_personal_tenant(db):
    from src.core.db import Tenant

    first = find_or_create_user(db, _identity())
    find_or_create_user(db, _identity(name="Octo Updated"))  # returning-user branch

    tenants = db.query(Tenant).filter(Tenant.kind == "personal", Tenant.personal_user_id == first.id).all()
    assert len(tenants) == 1


def test_refuses_to_auto_link_an_existing_email_registered_account(db):
    # Self-registration has no email verification, so silently linking a GitHub identity
    # onto an existing account by email match alone would let an attacker who
    # pre-registered a victim's email inherit their real GitHub identity.
    existing = User(email="owner@example.com", name="Owner", password_hash="x", is_workspace_admin=True, email_verified=True)
    db.add(existing)
    db.commit()
    db.refresh(existing)

    with pytest.raises(EmailAlreadyRegistered):
        find_or_create_user(db, _identity(github_user_id=555, email="owner@example.com"))

    db.refresh(existing)
    assert existing.github_user_id is None
    assert db.query(User).count() == 1


def test_unverified_self_registration_is_replaced_by_the_github_identity(db):
    # #1104: an unverified, GitHub-less registration proved nothing about the address, so the real owner
    # (GitHub-verified) gets a fresh account instead of being locked out by a squatter.
    from datetime import datetime, timedelta, timezone

    from src.core.db import Tenant, UserSession

    squatter = User(email="victim@example.com", password_hash="attacker-hash", email_verified=False, email_verify_token="tok")
    db.add(squatter)
    db.commit()
    squatter_id = squatter.id
    tenant_repo.ensure_personal_tenant(db, squatter_id)
    db.add(UserSession(jti="j1", user_id=squatter_id, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()

    user = find_or_create_user(db, _identity(github_user_id=777, email="Victim@Example.com", login="victim"))

    assert user.id != squatter_id
    assert user.github_user_id == 777 and user.email_verified is True and user.password_hash is None
    assert db.query(User).filter(User.id == squatter_id).count() == 0  # the squatter's row, sessions and tenant are gone
    assert db.query(UserSession).filter(UserSession.user_id == squatter_id).count() == 0
    assert db.query(Tenant).filter(Tenant.personal_user_id == squatter_id).count() == 0
    assert db.query(Tenant).filter(Tenant.personal_user_id == user.id).count() == 1


def test_replacement_is_audited_when_a_concurrent_callback_won_the_identity(db):
    # The squatter's deletion is committed on the "another callback created this identity while we waited for the
    # lock" path too, so that path records the replacement as well.
    import json

    from src.core.db import AuditLog
    from src.services import account_deletion

    squatter = User(email="victim@example.com", password_hash="x", email_verified=False)
    db.add(squatter)
    db.commit()
    squatter_id = squatter.id
    tenant_repo.ensure_personal_tenant(db, squatter_id)
    real_delete = account_deletion.delete_account

    def delete_then_lose_the_race(session, user):
        real_delete(session, user)
        winner = User(email="other@example.com", password_hash=None, email_verified=True, github_user_id=777)
        session.add(winner)
        session.flush()
        tenant_repo.ensure_personal_tenant(session, winner.id, commit=False)

    with patch.object(account_deletion, "delete_account", delete_then_lose_the_race):
        user = find_or_create_user(db, _identity(github_user_id=777, email="victim@example.com"))

    assert user.email == "other@example.com"
    rows = db.query(AuditLog).filter(AuditLog.action == "auth.unverified_registration_replaced").all()
    assert len(rows) == 1 and json.loads(rows[0].payload)["replaced_user_id"] == squatter_id


def test_unverified_account_already_linked_to_another_github_id_is_not_claimed(db):
    existing = User(email="owner@example.com", password_hash=None, email_verified=False, github_user_id=1)
    db.add(existing)
    db.commit()

    with pytest.raises(EmailAlreadyRegistered):
        find_or_create_user(db, _identity(github_user_id=2, email="owner@example.com"))


def test_refuses_to_auto_link_when_existing_email_differs_only_by_case(db):
    # The email-collision check must be case-insensitive, same as the users.email
    # uniqueness constraint itself.
    existing = User(email="owner@example.com", name="Owner", password_hash="x", is_workspace_admin=True, email_verified=True)
    db.add(existing)
    db.commit()

    with pytest.raises(EmailAlreadyRegistered):
        find_or_create_user(db, _identity(github_user_id=555, email="Owner@Example.com"))


def test_unrelated_email_still_creates_a_new_user(db):
    existing = User(email="owner@example.com", name="Owner", password_hash="x", is_workspace_admin=True)
    db.add(existing)
    db.commit()

    created = find_or_create_user(db, _identity(github_user_id=555, email="someone-else@example.com"))
    assert created.id != existing.id
    assert created.github_user_id == 555
    assert db.query(User).count() == 2


def test_idempotent_by_github_id(db):
    first = find_or_create_user(db, _identity())
    again = find_or_create_user(db, _identity(name="Octo Updated"))
    assert again.id == first.id
    assert db.query(User).count() == 1


def test_new_github_user_is_created_already_verified(db):
    # GitHub already vouches for the identity's email, so a GitHub-created account
    # shouldn't need to click an emailed verification link too.
    user = find_or_create_user(db, _identity())
    assert user.email_verified is True


def test_concurrent_oauth_callback_for_same_identity_recovers_the_winner(db):
    # Simulates two near-simultaneous OAuth callbacks for the same new GitHub identity:
    # the winner's row is already committed, and the loser's insert collides on the
    # github_user_id unique constraint. Unlike /auth/register, login should recover
    # gracefully by re-querying for the winner's row instead of 500ing or 409ing.
    winner = find_or_create_user(db, _identity())

    real_first = Query.first
    calls = {"n": 0}

    def racy_first(self):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None
        return real_first(self)

    with patch.object(Query, "first", racy_first):
        loser = find_or_create_user(db, _identity(name="Octo Loser"))

    assert loser.id == winner.id
    assert db.query(User).filter(User.github_user_id == 1001).count() == 1


def test_concurrent_identity_vs_existing_email_raises_email_already_registered(db):
    # The flush's IntegrityError isn't always a github_user_id collision -- a competing
    # request can grab this identity's email in the gap between the upfront check and the
    # flush. Recovery must fall back to EmailAlreadyRegistered, not leak a raw 500.
    existing = User(email="shared@example.com", name="Existing", password_hash="x", is_workspace_admin=True)
    db.add(existing)
    db.commit()

    real_first = Query.first
    calls = {"n": 0}

    def racy_first(self):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None
        return real_first(self)

    with patch.object(Query, "first", racy_first):
        with pytest.raises(EmailAlreadyRegistered):
            find_or_create_user(db, _identity(github_user_id=9999, email="shared@example.com"))

    assert db.query(User).filter(User.github_user_id == 9999).count() == 0


def test_login_redirects_to_github(gh_client, oauth_configured):
    resp = gh_client.get("/auth/github/login", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://github.com/login/oauth/authorize")
    assert "clevis_oauth_state" in resp.headers.get("set-cookie", "")


def test_login_unconfigured_redirects_to_ui_with_error(gh_client, monkeypatch):
    # Force unconfigured regardless of the ambient .env (a real App may be configured locally).
    monkeypatch.setattr(settings, "github_app_client_id", None)
    monkeypatch.setattr(settings, "github_app_client_secret", None)
    resp = gh_client.get("/auth/github/login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=github_not_configured")


def test_callback_creates_user_and_sets_cookie(gh_client, db):
    with (
        patch("src.routers.github_auth.github_oauth.verify_state", return_value=True),
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships", return_value=None),
    ):
        resp = gh_client.get("/auth/github/callback?code=c&state=s", follow_redirects=False)
    assert resp.status_code == 303
    assert "clevis_session" in resp.headers.get("set-cookie", "")
    assert db.query(User).filter(User.github_user_id == 1001).first() is not None


def test_callback_rejects_bad_state(gh_client):
    with patch("src.routers.github_auth.github_oauth.verify_state", return_value=False):
        resp = gh_client.get("/auth/github/callback?code=c&state=bad", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=github_invalid_state")


def test_callback_redirects_with_error_when_email_already_registered(gh_client, db):
    existing = User(email="octo@example.com", name="Owner", password_hash="x", is_workspace_admin=True, email_verified=True)
    db.add(existing)
    db.commit()

    with (
        patch("src.routers.github_auth.github_oauth.verify_state", return_value=True),
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
    ):
        resp = gh_client.get("/auth/github/callback?code=c&state=s", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=github_email_registered")
    db.refresh(existing)
    assert existing.github_user_id is None


def test_callback_redirects_to_ui_when_oauth_becomes_unconfigured_mid_flow(gh_client):
    # e.g. an admin cleared GITHUB_APP_CLIENT_ID/SECRET between /login and /callback.
    with (
        patch("src.routers.github_auth.github_oauth.verify_state", return_value=True),
        patch(
            "src.routers.github_auth.github_oauth.exchange_code_for_token",
            side_effect=github_oauth.GitHubOAuthNotConfigured("not configured"),
        ),
    ):
        resp = gh_client.get("/auth/github/callback?code=c&state=s", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=github_not_configured")


def test_callback_redirects_to_ui_on_oauth_error(gh_client):
    from src.services.github_oauth import GitHubOAuthError

    with (
        patch("src.routers.github_auth.github_oauth.verify_state", return_value=True),
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", side_effect=GitHubOAuthError("bad_verification_code")),
    ):
        resp = gh_client.get("/auth/github/callback?code=c&state=s", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=github_oauth_failed")


def _extract_state(login_resp) -> str:
    return parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]


def test_full_flow_succeeds_when_the_state_cookie_matches(gh_client, db, oauth_configured):
    login_resp = gh_client.get("/auth/github/login", follow_redirects=False)
    state = _extract_state(login_resp)

    with (
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships", return_value=None),
    ):
        # Same client/cookie jar as /login -- the browser that started the flow.
        resp = gh_client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert "clevis_session" in resp.headers.get("set-cookie", "")


def test_replaying_a_captured_state_from_a_different_browser_is_rejected(gh_client, db, oauth_configured):
    # Simulates the attack this fix closes: an attacker captures a valid (code, state)
    # pair from their own OAuth flow and gets a victim to open the callback URL. The
    # victim's browser never had the matching clevis_oauth_state cookie set.
    login_resp = gh_client.get("/auth/github/login", follow_redirects=False)
    state = _extract_state(login_resp)
    gh_client.cookies.delete("clevis_oauth_state")

    resp = gh_client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=github_invalid_state")
    assert db.query(User).count() == 0


def test_login_embeds_next_into_state(gh_client, oauth_configured):
    login_resp = gh_client.get("/auth/github/login?next=/invite/abc123", follow_redirects=False)
    state = _extract_state(login_resp)
    assert github_oauth.decode_state_next(state) == "/invite/abc123"


def test_login_drops_unsafe_next(gh_client, oauth_configured):
    login_resp = gh_client.get("/auth/github/login?next=//evil.com", follow_redirects=False)
    state = _extract_state(login_resp)
    assert github_oauth.decode_state_next(state) is None


def test_callback_redirects_to_next_path_from_invite_link(gh_client, db, oauth_configured):
    login_resp = gh_client.get("/auth/github/login?next=/invite/abc123", follow_redirects=False)
    state = _extract_state(login_resp)

    with (
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships", return_value=None),
    ):
        resp = gh_client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "http://localhost:3000/invite/abc123"


def test_callback_redirects_to_root_when_no_next_was_requested(gh_client, db, oauth_configured):
    login_resp = gh_client.get("/auth/github/login", follow_redirects=False)
    state = _extract_state(login_resp)

    with (
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships", return_value=None),
    ):
        resp = gh_client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "http://localhost:3000"


def test_callback_ignores_unsafe_next_and_redirects_to_root(gh_client, db, oauth_configured):
    login_resp = gh_client.get("/auth/github/login?next=//evil.com", follow_redirects=False)
    state = _extract_state(login_resp)

    with (
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships", return_value=None),
    ):
        resp = gh_client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "http://localhost:3000"


def test_callback_clears_the_state_cookie_on_success(gh_client, db, oauth_configured):
    login_resp = gh_client.get("/auth/github/login", follow_redirects=False)
    state = _extract_state(login_resp)

    with (
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships", return_value=None),
    ):
        resp = gh_client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    # There are two Set-Cookie headers on this response (session + state-cookie-clear) --
    # use get_list so the second one isn't dropped by a naive single-value lookup.
    set_cookies = resp.headers.get_list("set-cookie")
    state_cookie_headers = [c for c in set_cookies if c.startswith("clevis_oauth_state=")]
    assert len(state_cookie_headers) == 1
    assert "Max-Age=0" in state_cookie_headers[0]


def test_registration_setting_is_read_from_the_database(db):
    from sqlalchemy import text

    from src.core.app_config import registration_enabled

    assert registration_enabled(db) is True  # no row -> code default
    db.execute(text("INSERT INTO app_config (key, value, updated_at) VALUES ('registration_enabled', 'false', NOW())"))
    assert registration_enabled(db) is False


# --- linking GitHub to a signed-in password account (#1007) --------------------------------------

def _password_user(db, email="pw@example.com", **fields):
    from src.core.db import User

    user = User(email=email, name=None, password_hash="x", is_workspace_admin=False, email_verified=True, **fields)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _session(user) -> str:
    from src.core.auth import create_access_token

    return create_access_token(user.id, user.email, False, None, user.token_version)


def _start_link(client, user, token=None):
    return client.post("/auth/github/link", headers={"Authorization": f"Bearer {token or _session(user)}"})


def _link_state(resp) -> str:
    return parse_qs(urlparse(resp.json()["url"]).query)["state"][0]


def _finish_link(client, state, identity=None, token="gho_x", oauth_error=None):
    exchange = {"side_effect": oauth_error} if oauth_error else {"return_value": token}
    with (
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", **exchange),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=identity or _identity()),
        patch("src.routers.github_auth.org_provisioning.sync_org_admin_memberships") as sync,
    ):
        resp = client.get(f"/auth/github/callback?code=c&state={state}", follow_redirects=False)
    return resp, sync


def test_link_start_requires_a_session_and_an_unlinked_configured_account(gh_client, db, oauth_configured, monkeypatch):
    assert gh_client.post("/auth/github/link").status_code == 401

    user = _password_user(db)
    started = _start_link(gh_client, user)
    assert started.status_code == 200
    parsed = urlparse(started.json()["url"])
    assert parsed.netloc == "github.com" and parsed.path == "/login/oauth/authorize"
    assert "clevis_oauth_state" in started.headers.get("set-cookie", "")
    state = _link_state(started)
    nonce = gh_client.cookies.get("clevis_oauth_state")
    assert github_oauth.verify_state(state, cookie_nonce=nonce, purpose=github_oauth.LINK_PURPOSE)
    uid, version, jti, sexp = github_oauth.decode_state_link(state)
    assert (uid, version) == (user.id, user.token_version) and jti and sexp  # the starting session's id and expiry are bound in
    # A link state is not a sign-in state, so it can never be replayed against the sign-in path.
    assert not github_oauth.verify_state(state, cookie_nonce=nonce)

    linked = _password_user(db, "linked@example.com", github_user_id=77, github_login="old")
    already = _start_link(gh_client, linked)
    assert already.status_code == 409 and "@old" in already.json()["detail"]

    monkeypatch.setattr(settings, "github_app_client_id", None)
    monkeypatch.setattr(settings, "github_app_client_secret", None)
    assert _start_link(gh_client, user).status_code == 409


def test_a_signed_in_password_user_links_github_and_gets_their_org_memberships(gh_client, db, oauth_configured):
    from src.core.db import AuditLog

    user = _password_user(db)
    token = _session(user)
    state = _link_state(_start_link(gh_client, user, token))
    # Password login sets no session cookie, so a clean browser has none when GitHub redirects back.
    assert gh_client.cookies.get("clevis_session") is None

    resp, sync = _finish_link(
        gh_client, state, _identity(github_user_id=555, login="octo", name="Octo Cat", avatar_url="http://a")
    )

    assert resp.status_code == 303 and resp.headers["location"].endswith("/settings?github=linked")
    db.refresh(user)
    assert (user.github_user_id, user.github_login, user.avatar_url, user.name) == (555, "octo", "http://a", "Octo Cat")
    # Linking changes neither the sign-in credentials nor the email, and does not start another session.
    assert user.email == "pw@example.com" and user.password_hash == "x"
    assert "clevis_session" not in resp.headers.get("set-cookie", "")
    sync.assert_called_once()
    assert sync.call_args.args[1].id == user.id and sync.call_args.args[2] == "gho_x"
    row = db.query(AuditLog).filter(AuditLog.action == "auth.github_linked").one()
    assert "octo" in row.payload


def test_a_browser_signed_in_as_another_account_or_signed_out_everywhere_cannot_finish_the_link(gh_client, db, oauth_configured):
    user = _password_user(db)
    other = _password_user(db, "other@example.com")

    # This browser carries a different account's session: the flow was not started by them.
    state = _link_state(_start_link(gh_client, user))
    gh_client.cookies.set("clevis_session", _session(other))
    resp, sync = _finish_link(gh_client, state)
    assert resp.headers["location"].endswith("/settings?github_link_error=session")
    gh_client.cookies.delete("clevis_session")

    # "Sign out of all devices" between starting and finishing cancels the flow.
    state = _link_state(_start_link(gh_client, user))
    user.token_version += 1
    db.commit()
    resp, sync = _finish_link(gh_client, state)
    assert resp.headers["location"].endswith("/settings?github_link_error=session")

    for u in (user, other):
        db.refresh(u)
        assert u.github_user_id is None
    sync.assert_not_called()


def test_logging_out_the_session_that_started_the_link_cancels_it(gh_client, db, oauth_configured):
    from datetime import datetime, timedelta, timezone

    import jwt as pyjwt

    from src.core.db import RevokedToken

    user = _password_user(db)
    token = _session(user)
    state = _link_state(_start_link(gh_client, user, token))
    jti = pyjwt.decode(token, options={"verify_signature": False})["jti"]
    # What POST /auth/logout writes for that session.
    db.add(RevokedToken(jti=jti, user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    db.commit()

    resp, sync = _finish_link(gh_client, state)

    assert resp.headers["location"].endswith("/settings?github_link_error=session")
    db.refresh(user)
    assert user.github_user_id is None
    sync.assert_not_called()


def test_a_link_state_from_another_browser_or_tampered_never_links(gh_client, db, oauth_configured):
    user = _password_user(db)
    token = _session(user)
    state = _link_state(_start_link(gh_client, user, token))
    gh_client.cookies.set("clevis_session", token)

    gh_client.cookies.set("clevis_oauth_state", "someone-elses-nonce")
    resp, _ = _finish_link(gh_client, state)
    assert resp.headers["location"].endswith("/login?error=github_invalid_state")
    resp, _ = _finish_link(gh_client, state + "x")
    assert resp.headers["location"].endswith("/login?error=github_invalid_state")
    db.refresh(user)
    assert user.github_user_id is None


def test_a_github_account_already_linked_to_someone_else_is_refused(gh_client, db, oauth_configured):
    owner = _password_user(db, "owner@example.com", github_user_id=555, github_login="octo")
    user = _password_user(db)
    token = _session(user)
    gh_client.cookies.set("clevis_session", token)

    state = _link_state(_start_link(gh_client, user, token))
    resp, sync = _finish_link(gh_client, state, _identity(github_user_id=555))

    assert resp.headers["location"].endswith("/settings?github_link_error=taken")
    db.refresh(user)
    db.refresh(owner)
    assert user.github_user_id is None and owner.github_user_id == 555
    sync.assert_not_called()


def test_an_account_already_linked_to_one_github_identity_cannot_be_moved_to_another(gh_client, db, oauth_configured):
    user = _password_user(db)
    token = _session(user)
    gh_client.cookies.set("clevis_session", token)
    state = _link_state(_start_link(gh_client, user, token))
    # Linked (for example in another tab) between start and callback, to a different GitHub identity.
    user.github_user_id, user.github_login = 900, "mine"
    db.commit()

    resp, sync = _finish_link(gh_client, state, _identity(github_user_id=901))

    assert resp.headers["location"].endswith("/settings?github_link_error=other_account")
    db.refresh(user)
    assert user.github_user_id == 900
    sync.assert_not_called()


def test_relinking_the_same_github_account_is_harmless_and_oauth_failures_are_reported(gh_client, db, oauth_configured):
    user = _password_user(db)
    token = _session(user)
    gh_client.cookies.set("clevis_session", token)

    state = _link_state(_start_link(gh_client, user, token))
    resp, _ = _finish_link(gh_client, state, oauth_error=github_oauth.GitHubOAuthError("denied"))
    assert resp.headers["location"].endswith("/settings?github_link_error=oauth")

    user.github_user_id, user.github_login = 555, "octo"
    db.commit()
    state = _link_state(_start_link(gh_client, user, token)) if False else None
    state, nonce = github_oauth.sign_state(link_user_id=user.id)
    gh_client.cookies.set("clevis_oauth_state", nonce)
    resp, _ = _finish_link(gh_client, state, _identity(github_user_id=555, login="octo-renamed"))
    assert resp.headers["location"].endswith("/settings?github=linked")
    db.refresh(user)
    assert (user.github_user_id, user.github_login) == (555, "octo-renamed")


def test_a_legacy_session_that_started_the_link_is_cancelled_by_logout_and_an_expired_one_cannot_finish(
    gh_client, db, oauth_configured
):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import patch as _patch

    import jwt as pyjwt

    from src.core.auth import _revocation_key
    from src.core.config import settings
    from src.core.db import RevokedToken

    user = _password_user(db)
    claims = pyjwt.decode(_session(user), options={"verify_signature": False})
    del claims["jti"]  # issued before migration 0050
    legacy = pyjwt.encode(claims, settings.auth_secret.get_secret_value(), algorithm="HS256")
    state = _link_state(_start_link(gh_client, user, legacy))
    db.add(
        RevokedToken(
            jti=_revocation_key(legacy, claims), user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
    )
    db.commit()
    resp, sync = _finish_link(gh_client, state)
    assert resp.headers["location"].endswith("/settings?github_link_error=session")
    sync.assert_not_called()

    # Past the starting session's own expiry the (purged) denylist can no longer vouch for it.
    user2 = _password_user(db, "pw2@example.com")
    state2 = _link_state(_start_link(gh_client, user2))
    with _patch("src.routers.github_auth.time") as fake_time:  # the state JWT keeps real time
        fake_time.time.return_value = 9_999_999_999
        resp, sync = _finish_link(gh_client, state2)
    assert resp.headers["location"].endswith("/settings?github_link_error=session")
    sync.assert_not_called()


def test_denying_authorization_on_github_returns_to_settings_not_login(gh_client, db, oauth_configured):
    user = _password_user(db)
    state = _link_state(_start_link(gh_client, user))
    resp = gh_client.get(f"/auth/github/callback?error=access_denied&state={state}", follow_redirects=False)
    assert resp.headers["location"].endswith("/settings?github_link_error=oauth")
