"""Tests for POST /orgs/{org_login}/security/remediate/bulk (org-admin only).

dry_run previews per repo what the fix would do without writing; apply fixes only the repos that
need it and captures per-repo failures.
"""

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.remediation_bulk import router

SS = "repository_secret_scanning_enabled"
BP = "repository_default_branch_protection_enabled"
FP = "repository_default_branch_no_force_push"


def _status_error(code: int, method: str = "GET") -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        str(code), request=httpx.Request(method, "https://api.github.com"), response=httpx.Response(code)
    )


def _protection(*, force_push=False, restrictions=None):
    return {
        "required_status_checks": None,
        "enforce_admins": {"enabled": False},
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "restrictions": restrictions,
        "allow_force_pushes": {"enabled": force_push},
        "allow_deletions": {"enabled": False},
        "required_linear_history": {"enabled": False},
        "block_creations": {"enabled": False},
        "required_conversation_resolution": {"enabled": False},
    }


class FakeGitHub:
    """Per-repo state; records every write so tests can assert what was (not) touched."""

    def __init__(self, repos: dict[str, dict]):
        self.repos = repos
        self.writes: list[tuple[str, str]] = []

    def request(self, method, path, params=None, json=None):
        parts = path.strip("/").split("/")  # repos/{owner}/{repo}[/branches/{b}/protection]
        repo = parts[2]
        state = self.repos[repo]
        if "error" in state:
            raise state["error"]
        if method == "GET" and len(parts) == 3:
            return {
                "default_branch": "main",
                "security_and_analysis": state.get("security_and_analysis"),
            }
        if parts[-1] == "protection":
            if method == "PUT":
                self.writes.append((repo, "PUT protection"))
                state["protection"] = json
                return {}
            if state.get("protection") is None:
                raise _status_error(404)
            return state["protection"]
        if method == "PATCH":
            self.writes.append((repo, "PATCH repo"))
            return {}
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture()
def acme(db):
    admin = User(email="admin@e.com", name=None, password_hash=None, is_workspace_admin=False)
    member = User(email="member@e.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add_all([admin, member])
    db.commit()
    db.refresh(admin)
    db.refresh(member)
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member}


def _client(db, user_id, email="admin@e.com"):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user_id, email=email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _post(client, body):
    return client.post("/orgs/acme/security/remediate/bulk", json={"token": "ghp_admin", **body})


def _run(db, acme, gh: FakeGitHub, body):
    client = _client(db, acme["admin"].id)
    with patch("src.routers.remediation_bulk.GitHubClient", return_value=gh):
        return _post(client, body)


def _by_repo(resp):
    return {i["repo"]: i for i in resp.json()["items"]}


# --- secret scanning ------------------------------------------------------

_ENABLED = {"secret_scanning": {"status": "enabled"}}
_DISABLED = {"secret_scanning": {"status": "disabled"}}


def test_dry_run_previews_secret_scanning_per_repo_without_writing(db, acme):
    gh = FakeGitHub({"on": {"security_and_analysis": _ENABLED}, "off": {"security_and_analysis": _DISABLED}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["on", "off"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True and body["check_id"] == SS
    items = _by_repo(resp)
    assert items["on"]["status"] == "unchanged" and "already enabled" in items["on"]["detail"]
    assert items["off"]["status"] == "would_change"
    assert gh.writes == []


def test_apply_enables_secret_scanning_only_where_it_is_off(db, acme):
    gh = FakeGitHub({"on": {"security_and_analysis": _ENABLED}, "off": {"security_and_analysis": _DISABLED}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["on", "off"], "dry_run": False})

    items = _by_repo(resp)
    assert items["off"]["status"] == "applied"
    assert items["on"]["status"] == "unchanged"
    assert gh.writes == [("off", "PATCH repo")]  # the already-enabled repo is never touched


def test_secret_scanning_state_hidden_from_the_token_still_gets_the_fix(db, acme):
    gh = FakeGitHub({"blind": {"security_and_analysis": None}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["blind"], "dry_run": False})

    assert _by_repo(resp)["blind"]["status"] == "applied"
    assert gh.writes == [("blind", "PATCH repo")]


# --- branch protection ----------------------------------------------------


def test_unprotected_default_branch_gets_the_conservative_protection(db, acme):
    gh = FakeGitHub({"bare": {"protection": None}})
    preview = _run(db, acme, gh, {"check_id": BP, "repos": ["bare"]})
    assert _by_repo(preview)["bare"]["status"] == "would_change"
    assert "no protection" in _by_repo(preview)["bare"]["detail"]
    assert gh.writes == []

    resp = _run(db, acme, gh, {"check_id": BP, "repos": ["bare"], "dry_run": False})
    assert _by_repo(resp)["bare"]["status"] == "applied"
    assert gh.writes == [("bare", "PUT protection")]


def test_already_protected_repo_is_left_alone_for_the_protection_check(db, acme):
    # "protected" passes the check already; the batch must not rewrite its rules just because it
    # was selected (the single-repo fix would also turn force-pushes off).
    gh = FakeGitHub({"prot": {"protection": _protection(force_push=True)}})
    resp = _run(db, acme, gh, {"check_id": BP, "repos": ["prot"], "dry_run": False})

    assert _by_repo(resp)["prot"]["status"] == "unchanged"
    assert gh.writes == []


def test_force_push_check_turns_force_pushes_off_and_skips_repos_that_already_block_them(db, acme):
    gh = FakeGitHub({
        "loose": {"protection": _protection(force_push=True)},
        "tight": {"protection": _protection(force_push=False)},
    })
    resp = _run(db, acme, gh, {"check_id": FP, "repos": ["loose", "tight"], "dry_run": False})

    items = _by_repo(resp)
    assert items["loose"]["status"] == "applied"
    assert items["tight"]["status"] == "unchanged" and "already blocked" in items["tight"]["detail"]
    assert gh.writes == [("loose", "PUT protection")]
    assert gh.repos["loose"]["protection"]["allow_force_pushes"] is False


def test_a_branch_with_push_restrictions_is_reported_and_left_alone(db, acme):
    restricted = _protection(force_push=True, restrictions={"users": [{"login": "alice"}], "teams": [], "apps": []})
    gh = FakeGitHub({"locked": {"protection": restricted}, "bare": {"protection": None}})

    for dry_run in (True, False):
        resp = _run(db, acme, gh, {"check_id": FP, "repos": ["locked", "bare"], "dry_run": dry_run})
        items = _by_repo(resp)
        assert items["locked"]["status"] == "failed" and "restricts who can push" in items["locked"]["detail"]
    assert ("locked", "PUT protection") not in gh.writes


# --- failures, validation, auth, audit ------------------------------------


def test_one_repo_failing_does_not_abort_the_rest(db, acme):
    gh = FakeGitHub({
        "good": {"security_and_analysis": _DISABLED},
        "gone": {"error": _status_error(404)},
        "boom": {"error": httpx.ConnectError("down")},
    })
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["gone", "good", "boom"], "dry_run": False})

    assert resp.status_code == 200
    items = _by_repo(resp)
    assert items["good"]["status"] == "applied"
    assert items["gone"] == {"repo": "gone", "status": "failed", "detail": "GitHub API error: 404"}
    assert items["boom"]["status"] == "failed" and items["boom"]["detail"] == "GitHub API unreachable"


@pytest.mark.parametrize("dry_run", [True, False])
def test_every_repo_403_adds_the_permission_hint_but_keeps_the_per_repo_results(db, acme, dry_run):
    gh = FakeGitHub({"a": {"error": _status_error(403)}, "b": {"error": _status_error(403)}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["a", "b"], "dry_run": dry_run})

    assert resp.status_code == 200
    assert "Administration" in resp.json()["hint"]
    assert {i["repo"]: i["status"] for i in resp.json()["items"]} == {"a": "failed", "b": "failed"}


def test_a_lone_repo_403_is_not_blamed_on_the_apps_permissions(db, acme):
    # one archived or ungranted repo is not evidence that the App lacks Administration
    gh = FakeGitHub({"only": {"error": _status_error(403)}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["only"]})

    assert resp.status_code == 200
    assert resp.json()["hint"] is None
    assert _by_repo(resp)["only"] == {"repo": "only", "status": "failed", "detail": "GitHub API error: 403"}


def test_a_single_403_among_successes_is_just_that_repo_failing(db, acme):
    gh = FakeGitHub({"a": {"error": _status_error(403)}, "b": {"security_and_analysis": _DISABLED}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["a", "b"]})

    assert resp.status_code == 200
    assert resp.json()["hint"] is None
    assert _by_repo(resp)["a"]["status"] == "failed" and _by_repo(resp)["b"]["status"] == "would_change"


@pytest.mark.parametrize(("dry_run", "ok_status"), [(True, "would_change"), (False, "applied")])
def test_an_invalid_repo_name_is_rejected_per_repo_without_calling_github(db, acme, dry_run, ok_status):
    gh = FakeGitHub({"api": {"security_and_analysis": _DISABLED}})
    resp = _run(db, acme, gh, {"check_id": SS, "repos": ["../other/x", "api"], "dry_run": dry_run})

    items = _by_repo(resp)
    assert items["../other/x"] == {"repo": "../other/x", "status": "failed", "detail": "invalid repository name"}
    assert items["api"]["status"] == ok_status


def test_unknown_check_id_is_404(db, acme):
    resp = _run(db, acme, FakeGitHub({}), {"check_id": "not_a_real_check", "repos": ["api"]})
    assert resp.status_code == 404


def test_check_without_an_automated_fix_is_404(db, acme):
    # open Dependabot alerts can't be fixed by flipping a setting
    resp = _run(db, acme, FakeGitHub({}), {"check_id": "repository_dependabot_alerts_clear", "repos": ["api"]})
    assert resp.status_code == 404


def test_member_is_forbidden(db, acme):
    client = _client(db, acme["member"].id, email="member@e.com")
    assert _post(client, {"check_id": SS, "repos": ["api"]}).status_code == 403


def test_no_token_available_returns_400(db, acme):
    client = _client(db, acme["admin"].id)
    resp = client.post("/orgs/acme/security/remediate/bulk", json={"check_id": SS, "repos": ["api"]})
    assert resp.status_code == 400


def test_the_repo_list_is_bounded(db, acme):
    client = _client(db, acme["admin"].id)
    assert _post(client, {"check_id": SS, "repos": []}).status_code == 422
    assert _post(client, {"check_id": SS, "repos": [f"r{i}" for i in range(101)]}).status_code == 422

    names = [f"r{i}" for i in range(100)]
    gh = FakeGitHub({n: {"security_and_analysis": _ENABLED} for n in names})
    assert _run(db, acme, gh, {"check_id": SS, "repos": names}).status_code == 200  # the cap itself is allowed


@pytest.mark.parametrize(
    ("dry_run", "action"),
    [(True, "security.remediate.bulk_dryrun"), (False, "security.remediate.bulk_apply")],
)
def test_each_run_writes_an_audit_row_under_the_orgs_tenant(db, acme, dry_run, action):
    gh = FakeGitHub({"api": {"security_and_analysis": _ENABLED}})
    _run(db, acme, gh, {"check_id": SS, "repos": ["api"], "dry_run": dry_run})

    row = db.query(AuditLog).filter(AuditLog.action == action).one()
    assert row.actor == "admin@e.com" and row.target == "acme"
    assert row.tenant_id == acme["org"].tenant_id
    assert '"check_id": "repository_secret_scanning_enabled"' in row.payload
    assert '"repos": ["api"]' in row.payload


def test_the_audit_row_is_written_even_when_every_repo_fails(db, acme):
    gh = FakeGitHub({"a": {"error": _status_error(403)}})
    _run(db, acme, gh, {"check_id": SS, "repos": ["a"], "dry_run": False})

    assert db.query(AuditLog).filter(AuditLog.action == "security.remediate.bulk_apply").count() == 1


def test_an_apply_also_records_what_actually_happened_per_repo(db, acme):
    gh = FakeGitHub({
        "fixed": {"security_and_analysis": _DISABLED},
        "fine": {"security_and_analysis": _ENABLED},
        "gone": {"error": _status_error(404)},
    })
    _run(db, acme, gh, {"check_id": SS, "repos": ["fixed", "fine", "gone"], "dry_run": False})

    row = db.query(AuditLog).filter(AuditLog.action == "security.remediate.bulk_result").one()
    assert row.tenant_id == acme["org"].tenant_id and row.target == "acme"
    import json

    assert json.loads(row.payload) == {
        "check_id": SS,
        "applied": ["fixed"],
        "unchanged": ["fine"],
        "failed": {"gone": "GitHub API error: 404"},
    }


def test_a_preview_writes_no_result_row(db, acme):
    gh = FakeGitHub({"api": {"security_and_analysis": _DISABLED}})
    _run(db, acme, gh, {"check_id": SS, "repos": ["api"], "dry_run": True})

    assert db.query(AuditLog).filter(AuditLog.action == "security.remediate.bulk_result").count() == 0


# --- service-level edge cases the route can't reach -----------------------


def test_needs_change_rejects_a_check_without_an_automated_fix():
    from src.services import check_remediation, check_remediation_bulk

    with pytest.raises(check_remediation.RemediationNotSupported):
        check_remediation_bulk.needs_change(FakeGitHub({"api": {}}), "repository_dependabot_alerts_clear", "acme", "api")


def test_a_non_object_repo_payload_is_treated_as_empty():
    from src.services import check_remediation_bulk

    class Odd:
        def request(self, method, path, params=None, json=None):
            return []  # not the dict GitHub documents

    needed, detail = check_remediation_bulk.needs_change(Odd(), SS, "acme", "api")
    assert needed is True and "isn't visible" in detail
