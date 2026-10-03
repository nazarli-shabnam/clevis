"""Tests for API tokens, the machine score endpoints, the score badge and the CI action script."""

import importlib.util
import json
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
    with patch.object(check, "fetch_score", side_effect=OSError("x")), pytest.raises(OSError):
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
