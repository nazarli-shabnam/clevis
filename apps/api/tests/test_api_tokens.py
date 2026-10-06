"""Tests for API tokens, the machine score endpoints, the score badge and the CI action script."""

import http.client
import importlib.util
import json
import ssl
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import ApiToken, AuditLog, User, get_db
from src.repositories import api_token_repo, org_membership_repo, org_repo, scan_results_repo
from src.routers.api_tokens import router as tokens_router

CHECKS = [
    {"id": "repository_secret_scanning_enabled", "title": "Secret scanning", "status": "fail", "value": {"secret": "x"}},
    {"id": "organization_members_mfa_required", "title": "MFA", "status": "pass", "value": True},
]


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


def _client(db, user=None):
    app = FastAPI()
    app.include_router(tokens_router)
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[require_auth] = lambda: user
        db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


@pytest.fixture()
def acme(db):
    admin = _make_user(db, "tok-admin@e.com")
    member = _make_user(db, "tok-member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def _new_token(db, acme) -> str:
    resp = _client(db, acme["admin"]).post("/orgs/acme/api-tokens", json={"name": "ci"})
    assert resp.status_code == 201
    return resp.json()["token"]


@pytest.fixture(autouse=True)
def _reset_badge_state():
    from src.core import rate_limit
    from src.routers import api_tokens

    api_tokens._badge_cache.clear()
    rate_limit._account_buckets.clear()
    yield
    api_tokens._badge_cache.clear()
    rate_limit._account_buckets.clear()


def _scan(db, org, score=80, checks=CHECKS):
    scan_results_repo.insert(
        db, owner="acme", score=score, total_checks=len(checks), failed_checks=1, checks=checks, tenant_id=org.tenant_id
    )


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# --- management ---

def test_create_returns_the_token_once_and_stores_only_its_hash(db, acme):
    resp = _client(db, acme["admin"]).post("/orgs/acme/api-tokens", json={"name": "ci"})

    body = resp.json()
    assert body["token"].startswith("clv_") and body["scope"] == "read"
    row = db.get(ApiToken, body["id"])
    assert row.token_hash == api_token_repo.hash_token(body["token"])
    assert body["token"] not in json.dumps({"hash": row.token_hash, "prefix": row.prefix})
    listing = _client(db, acme["admin"]).get("/orgs/acme/api-tokens").json()
    assert "token" not in listing[0] and listing[0]["prefix"] == body["token"][:8]
    log = db.query(AuditLog).filter(AuditLog.action == "api_token.created").one()
    assert body["token"] not in log.payload


def test_management_requires_org_admin(db, acme):
    client = _client(db, acme["member"])
    assert client.post("/orgs/acme/api-tokens", json={"name": "ci"}).status_code == 403
    assert client.get("/orgs/acme/api-tokens").status_code == 403


def test_active_token_cap(db, acme):
    client = _client(db, acme["admin"])
    for i in range(20):
        assert client.post("/orgs/acme/api-tokens", json={"name": f"t{i}"}).status_code == 201
    assert client.post("/orgs/acme/api-tokens", json={"name": "extra"}).status_code == 409


def test_revoked_token_stops_working_and_revoke_is_audited(db, acme):
    token = _new_token(db, acme)
    _scan(db, acme["org"])
    machine = _client(db)
    assert machine.get("/api/v1/orgs/acme/score", headers=_bearer(token)).status_code == 200

    token_id = _client(db, acme["admin"]).get("/orgs/acme/api-tokens").json()[0]["id"]
    assert _client(db, acme["admin"]).delete(f"/orgs/acme/api-tokens/{token_id}").status_code == 204

    assert _client(db).get("/api/v1/orgs/acme/score", headers=_bearer(token)).status_code == 401
    assert db.query(AuditLog).filter(AuditLog.action == "api_token.revoked").count() == 1
    assert _client(db, acme["admin"]).delete("/orgs/acme/api-tokens/99999").status_code == 404


# --- machine endpoints ---

def test_score_returns_latest_scan_without_check_values(db, acme):
    token = _new_token(db, acme)
    _scan(db, acme["org"], score=50)
    _scan(db, acme["org"], score=80)

    resp = _client(db).get("/api/v1/orgs/acme/score", headers=_bearer(token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["score"] == 80 and body["org"] == "acme"
    assert [c["status"] for c in body["checks"]] == ["fail", "pass"]
    assert all(set(c) == {"id", "title", "status"} for c in body["checks"])  # no per-check values
    assert db.query(ApiToken).one().last_used_at is not None


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer clv_nope"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}])
def test_score_rejects_missing_or_unknown_credentials(db, acme, headers):
    _scan(db, acme["org"])
    assert _client(db).get("/api/v1/orgs/acme/score", headers=headers).status_code == 401


def test_token_cannot_read_another_orgs_score(db, acme):
    token = _new_token(db, acme)
    other = org_repo.get_or_create(db, github_login="other")
    scan_results_repo.insert(db, owner="other", score=10, total_checks=1, failed_checks=1, checks=[], tenant_id=other.tenant_id)

    assert _client(db).get("/api/v1/orgs/other/score", headers=_bearer(token)).status_code == 404


def test_session_jwt_style_bearer_is_not_accepted_as_an_api_token(db, acme):
    _scan(db, acme["org"])
    assert _client(db).get("/api/v1/orgs/acme/score", headers=_bearer("eyJhbGciOi.fake.jwt")).status_code == 401


def test_score_404_before_any_scan(db, acme):
    token = _new_token(db, acme)
    assert _client(db).get("/api/v1/orgs/acme/score", headers=_bearer(token)).status_code == 404


def test_scan_runs_overview_persists_and_returns_it(db, acme):
    token = _new_token(db, acme)
    overview = {"owner": "acme", "score": 70, "total_checks": 2, "failed_checks": 1, "repo_count": 3, "checks": CHECKS}
    with (
        patch("src.routers.api_tokens.resolve_org_token", return_value="ghs_x"),
        patch("src.routers.analytics.get_overview", return_value=overview),
    ):
        resp = _client(db).post("/api/v1/orgs/acme/scan", headers=_bearer(token))

    assert resp.status_code == 200 and resp.json()["score"] == 70
    assert scan_results_repo.latest_with_checks(db, "acme", acme["org"].tenant_id)["score"] == 70


def test_scan_without_github_credentials_is_a_400(db, acme):
    token = _new_token(db, acme)
    resp = _client(db).post("/api/v1/orgs/acme/scan", headers=_bearer(token))
    assert resp.status_code == 400


def test_score_finds_a_scan_stored_under_a_different_owner_case(db, acme):
    token = _new_token(db, acme)
    scan_results_repo.insert(
        db, owner="Acme", score=66, total_checks=1, failed_checks=0, checks=[], tenant_id=acme["org"].tenant_id
    )
    resp = _client(db).get("/api/v1/orgs/acme/score", headers=_bearer(token))
    assert resp.status_code == 200 and resp.json()["score"] == 66


# --- badge ---

def test_badge_is_opt_in_and_shows_only_the_score(db, acme):
    _scan(db, acme["org"], score=95)
    public = _client(db)
    assert public.get("/badges/acme/score.svg").status_code == 404  # off by default

    admin = _client(db, acme["admin"])
    assert admin.put("/orgs/acme/badge", json={"enabled": True}).json() == {"enabled": True}
    resp = _client(db).get("/badges/ACME/score.svg")  # login match is case-insensitive

    assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/svg+xml")
    assert "95" in resp.text and "#4c1" in resp.text
    assert "max-age=300" in resp.headers["cache-control"]
    assert db.query(AuditLog).filter(AuditLog.action == "badge.enabled").count() == 1

    admin.put("/orgs/acme/badge", json={"enabled": False})
    assert _client(db).get("/badges/acme/score.svg").status_code == 404


def test_badge_reads_are_cached_and_opting_out_takes_effect_at_once(db, acme):
    _scan(db, acme["org"], score=95)
    admin = _client(db, acme["admin"])
    admin.put("/orgs/acme/badge", json={"enabled": True})
    public = _client(db)
    assert "95" in public.get("/badges/acme/score.svg").text

    _scan(db, acme["org"], score=40)  # newer scan, but the cached answer is still served
    assert "95" in public.get("/badges/acme/score.svg").text

    admin.put("/orgs/acme/badge", json={"enabled": False})  # invalidates the cache entry
    assert public.get("/badges/acme/score.svg").status_code == 404


def test_badge_cache_misses_are_rate_limited_per_ip_not_per_org(db, acme):
    public = _client(db)
    codes = [public.get(f"/badges/org-{i}/score.svg").status_code for i in range(62)]
    assert codes[:60] == [404] * 60
    assert codes[60:] == [429, 429]  # cycling through org names doesn't dodge the limit
    assert public.get("/badges/org-0/score.svg").status_code == 404  # cached answers stay free


def test_badge_404s_look_identical_for_unknown_and_never_scanned_orgs(db, acme):
    admin = _client(db, acme["admin"])
    admin.put("/orgs/acme/badge", json={"enabled": True})  # enabled but never scanned
    a = _client(db).get("/badges/acme/score.svg")
    b = _client(db).get("/badges/does-not-exist/score.svg")
    assert (a.status_code, a.text) == (b.status_code, b.text) == (404, a.text)


def test_badge_setting_requires_admin(db, acme):
    assert _client(db, acme["member"]).put("/orgs/acme/badge", json={"enabled": True}).status_code == 403


# --- CI action script ---

def _load_check():
    path = Path(__file__).resolve().parents[3] / "actions" / "clevis-score" / "check.py"
    spec = importlib.util.spec_from_file_location("clevis_score_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_action_evaluate_threshold_and_named_checks():
    check = _load_check()
    data = {"score": 80, "checks": [{"id": "a", "title": "A", "status": "fail"}, {"id": "b", "title": "B", "status": "pass"}]}

    assert check.evaluate(data, 80, []) == []
    assert "below the threshold" in check.evaluate(data, 90, [])[0]
    assert "'a'" in check.evaluate(data, 0, ["a", "b"])[0] and len(check.evaluate(data, 0, ["a", "b"])) == 1
    assert "not found" in check.evaluate(data, 0, ["missing"])[0]
    assert check.evaluate({"checks": []}, 0, []) == ["response did not contain a numeric score"]


def test_action_main_exit_codes(tmp_path):
    check = _load_check()
    env = {"CLEVIS_API_URL": "https://c.example", "CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_x", "CLEVIS_THRESHOLD": "75",
           "GITHUB_OUTPUT": str(tmp_path / "out")}
    with patch.object(check, "fetch_score", return_value={"score": 80, "checks": []}) as fetch:
        assert check.main(env) == 0
        fetch.assert_called_once_with("https://c.example", "acme", "clv_x", False)
    assert (tmp_path / "out").read_text() == "score=80\n"
    with patch.object(check, "fetch_score", return_value={"score": 60, "checks": []}):
        assert check.main(env) == 1
    with patch.object(check, "fetch_score", side_effect=RuntimeError("x")), pytest.raises(RuntimeError):
        check.main(env)  # unexpected errors are not swallowed into a pass
    assert check.main({**env, "CLEVIS_TOKEN": ""}) == 2
    assert check.main({**env, "CLEVIS_THRESHOLD": "abc"}) == 2


def test_action_main_maps_http_and_network_errors_to_exit_2(capsys):
    import urllib.error

    check = _load_check()
    env = {"CLEVIS_API_URL": "https://c.example", "CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_secret"}
    http_err = urllib.error.HTTPError("https://c.example", 401, "no", {}, None)
    with patch.object(check, "fetch_score", side_effect=http_err):
        assert check.main(env) == 2
    with patch.object(check, "fetch_score", side_effect=urllib.error.URLError("down")):
        assert check.main(env) == 2
    assert "clv_secret" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("reset"),
        ssl.SSLError("bad record mac"),
        http.client.IncompleteRead(b"par"),
        TimeoutError("slow"),
        json.JSONDecodeError("bad", "", 0),
    ],
)
def test_action_read_errors_exit_2_not_a_traceback_that_looks_like_a_failed_gate(error, capsys):
    check = _load_check()
    env = {"CLEVIS_API_URL": "https://c.example", "CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_secret"}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            raise error

    with patch("urllib.request.OpenerDirector.open", return_value=_Resp()):
        assert check.main(env) == 2  # a real body-read failure, not a patched fetch_score
    err = capsys.readouterr().err
    expected = "unreadable response" if isinstance(error, json.JSONDecodeError) else "Could not reach the Clevis API"
    assert expected in err


def test_action_rejects_a_non_object_response_with_exit_2():
    check = _load_check()
    env = {"CLEVIS_API_URL": "https://c.example", "CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_x"}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"[1, 2]"

    with patch("urllib.request.OpenerDirector.open", return_value=_Resp()):
        assert check.main(env) == 2


def test_action_error_annotations_cannot_smuggle_a_second_workflow_command(capsys):
    check = _load_check()
    env = {"CLEVIS_API_URL": "https://c.example", "CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_x", "CLEVIS_FAIL_ON_CHECKS": "a"}
    evil = "Evil\r\n::set-output name=pwned::1\n::error::fake"
    data = {"score": 100, "checks": [{"id": "a", "title": evil, "status": "fail"}]}
    with patch.object(check, "fetch_score", return_value=data):
        assert check.main(env) == 1

    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("::")]
    assert len(lines) == 1 and lines[0].startswith("::error::Clevis gate failed: ")


def test_action_never_sends_the_token_over_plain_http_or_to_a_redirect_target(capsys):
    import urllib.error

    check = _load_check()
    base = {"CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_secret"}
    for url in ("http://clevis.example.com", "ftp://clevis.example.com", "clevis.example.com"):
        with patch.object(check, "fetch_score") as fetch:
            assert check.main({**base, "CLEVIS_API_URL": url}) == 2
            fetch.assert_not_called()
    check.check_api_url("http://localhost:8080")  # local dev is allowed
    check.check_api_url("https://clevis.example.com")

    # A redirect is surfaced as an HTTP error instead of being followed with the Authorization header.
    req = urllib.request.Request("https://clevis.example.com/x", headers={"Authorization": "Bearer clv_secret"})
    handler = check._NoRedirect()
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/") is None
    assert "clv_secret" not in capsys.readouterr().err


# --- scope, expiry, last_used throttle, scan attribution ---

def test_token_with_an_unrecognised_scope_is_rejected_by_machine_endpoints(db, acme):
    token = _new_token(db, acme)
    _scan(db, acme["org"])
    db.execute(text("UPDATE api_tokens SET scope = 'write'"))

    machine = _client(db)
    assert machine.get("/api/v1/orgs/acme/score", headers=_bearer(token)).status_code == 403
    assert machine.post("/api/v1/orgs/acme/scan", headers=_bearer(token)).status_code == 403
    assert db.query(ApiToken).one().last_used_at is None  # a rejected token is not "used"


def test_expiring_token_works_until_it_expires_and_never_expires_by_default(db, acme):
    admin = _client(db, acme["admin"])
    expiring = admin.post("/orgs/acme/api-tokens", json={"name": "short", "expires_in_days": 7}).json()
    forever = admin.post("/orgs/acme/api-tokens", json={"name": "forever"}).json()
    assert expiring["expires_at"] is not None and forever["expires_at"] is None
    _scan(db, acme["org"])
    machine = _client(db)
    assert machine.get("/api/v1/orgs/acme/score", headers=_bearer(expiring["token"])).status_code == 200

    db.execute(text("UPDATE api_tokens SET expires_at = now() - interval '1 second' WHERE id = :id"), {"id": expiring["id"]})

    assert machine.get("/api/v1/orgs/acme/score", headers=_bearer(expiring["token"])).status_code == 401
    assert machine.get("/api/v1/orgs/acme/score", headers=_bearer(forever["token"])).status_code == 200


@pytest.mark.parametrize("days", [0, -1, 3651])
def test_expires_in_days_must_be_within_bounds(db, acme, days):
    resp = _client(db, acme["admin"]).post("/orgs/acme/api-tokens", json={"name": "ci", "expires_in_days": days})
    assert resp.status_code == 422


def test_last_used_at_is_written_at_most_once_a_minute(db, acme):
    token = _new_token(db, acme)
    _scan(db, acme["org"])
    machine = _client(db)
    machine.get("/api/v1/orgs/acme/score", headers=_bearer(token))
    first = db.query(ApiToken).one().last_used_at
    assert first is not None

    machine.get("/api/v1/orgs/acme/score", headers=_bearer(token))
    db.expire_all()
    assert db.query(ApiToken).one().last_used_at == first  # within the minute: not rewritten

    db.execute(text("UPDATE api_tokens SET last_used_at = now() - interval '2 minutes'"))
    machine.get("/api/v1/orgs/acme/score", headers=_bearer(token))
    db.expire_all()
    assert db.query(ApiToken).one().last_used_at > first - timedelta(minutes=1)


def test_scan_is_audited_under_the_token_and_so_are_its_alerts(db, acme):
    token = _new_token(db, acme)
    token_id = db.query(ApiToken).one().id
    _scan(db, acme["org"], score=90)
    overview = {"owner": "acme", "score": 60, "total_checks": 2, "failed_checks": 1, "repo_count": 3, "checks": CHECKS}
    with (
        patch("src.routers.api_tokens.resolve_org_token", return_value="ghs_x"),
        patch("src.routers.analytics.get_overview", return_value=overview),
        patch("src.routers.analytics.notifications.notify_score_drop") as notify,
    ):
        resp = _client(db).post("/api/v1/orgs/acme/scan", headers=_bearer(token))

    assert resp.status_code == 200
    entry = db.query(AuditLog).filter(AuditLog.action == "api_token.scan").one()
    assert entry.actor == f"api_token:{token_id}"
    assert json.loads(entry.payload) == {"token_id": token_id, "score": 60, "previous_score": 90}
    assert notify.call_args.kwargs["actor"] == f"api_token:{token_id}"


def test_expired_tokens_do_not_count_toward_the_active_cap(db, acme):
    client = _client(db, acme["admin"])
    for i in range(20):
        assert client.post("/orgs/acme/api-tokens", json={"name": f"t{i}", "expires_in_days": 1}).status_code == 201
    assert client.post("/orgs/acme/api-tokens", json={"name": "extra"}).status_code == 409

    db.execute(text("UPDATE api_tokens SET expires_at = now() - interval '1 hour'"))
    db.expire_all()  # the raw UPDATE bypasses the ORM identity map

    assert client.post("/orgs/acme/api-tokens", json={"name": "extra"}).status_code == 201


def test_a_failing_scan_audit_write_does_not_fail_the_stored_scan(db, acme):
    token = _new_token(db, acme)
    overview = {"owner": "acme", "score": 70, "total_checks": 2, "failed_checks": 1, "repo_count": 3, "checks": CHECKS}
    with (
        patch("src.routers.api_tokens.resolve_org_token", return_value="ghs_x"),
        patch("src.routers.analytics.get_overview", return_value=overview),
        patch("src.routers.api_tokens.audit_repo.write", side_effect=RuntimeError("audit down")),
    ):
        resp = _client(db).post("/api/v1/orgs/acme/scan", headers=_bearer(token))

    assert resp.status_code == 200 and resp.json()["score"] == 70


@pytest.mark.parametrize("checks", [None, 5, "x", [None, 3]])
def test_action_malformed_checks_field_is_handled_without_a_traceback(checks):
    check = _load_check()
    assert check.evaluate({"score": 90, "checks": checks}, 80, []) == []
    assert "not found" in check.evaluate({"score": 90, "checks": checks}, 80, ["a"])[0]


def test_action_score_summary_line_cannot_smuggle_a_workflow_command(capsys):
    check = _load_check()
    env = {"CLEVIS_API_URL": "https://c.example", "CLEVIS_ORG": "acme", "CLEVIS_TOKEN": "clv_x"}
    with patch.object(check, "fetch_score", return_value={"score": "1\n::error::fake", "checks": []}):
        assert check.main(env) == 1  # non-numeric score fails the gate

    out_lines = capsys.readouterr().out.splitlines()
    assert not any(line.startswith("::error::fake") for line in out_lines)


def test_an_in_flight_badge_lookup_cannot_repopulate_the_cache_after_an_opt_out(db):
    """A lookup that read the old (opted-in) score before an opt-out invalidated the entry must not write
    that score back afterwards, or the badge keeps showing for up to the cache TTL."""
    from types import SimpleNamespace

    from src.routers import api_tokens

    api_tokens._badge_cache.clear()

    class _SlowDb:
        def execute(self, *_args, **_kwargs):
            api_tokens._badge_invalidate("Acme")  # the opt-out lands while this read is in flight
            return SimpleNamespace(scalar=lambda: 80)

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"))

    assert api_tokens._badge_score(_SlowDb(), request, "Acme") == 80  # this request still gets its read
    assert "acme" not in api_tokens._badge_cache  # but the stale answer is not cached


def test_an_undisturbed_badge_lookup_is_still_cached(db):
    from types import SimpleNamespace

    from src.routers import api_tokens

    api_tokens._badge_cache.clear()
    calls = []

    class _Db:
        def execute(self, *_args, **_kwargs):
            calls.append(1)
            return SimpleNamespace(scalar=lambda: 91)

    request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.10"))

    assert api_tokens._badge_score(_Db(), request, "Quiet") == 91
    assert api_tokens._badge_score(_Db(), request, "Quiet") == 91
    assert len(calls) == 1


def test_token_lookup_policy_is_tied_to_the_table_owner_not_a_settable_flag(db):
    """A session setting like app.api_token_lookup can be set by any role, so the policy that lets
    resolve_api_token read api_tokens under FORCE RLS must key on identity (current_user), and the
    function must not rely on that setting."""
    qual = db.execute(
        text("SELECT qual FROM pg_policies WHERE tablename = 'api_tokens' AND policyname = 'token_lookup'")
    ).scalar()
    assert qual is not None and "current_user" in qual.lower() and "api_token_lookup" not in qual
    body = db.execute(text("SELECT prosrc || coalesce(array_to_string(proconfig, ','), '') FROM pg_proc WHERE proname = 'resolve_api_token'")).scalar()
    assert "api_token_lookup" not in body


def test_a_non_owner_session_cannot_unlock_api_tokens_by_setting_the_old_lookup_flag(db):
    if not db.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
        pytest.skip("needs a superuser connection to create a throwaway role")
    from src.repositories import org_repo

    org = org_repo.get_or_create(db, github_login="lookup-flag-org")
    api_token_repo.create(db, tenant_id=org.tenant_id, name="ci", created_by="t@e.com")
    db.flush()
    db.execute(text("CREATE ROLE rls_probe_reader NOSUPERUSER NOBYPASSRLS"))
    db.execute(text("GRANT USAGE ON SCHEMA public TO rls_probe_reader"))
    db.execute(text("GRANT SELECT ON api_tokens TO rls_probe_reader"))
    db.execute(text("RESET app.tenant_id"))
    db.execute(text("SET LOCAL ROLE rls_probe_reader"))
    try:
        db.execute(text("SET app.api_token_lookup = 'on'"))
        assert db.execute(text("SELECT count(*) FROM api_tokens")).scalar() == 0
    finally:
        db.execute(text("RESET ROLE"))
