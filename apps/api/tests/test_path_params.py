"""Per-repo routes must validate {owner}/{repo} so a decoded `?`, `#`, `%` or `..` never reaches a GitHub URL."""

from typing import get_args

import pytest
from fastapi.routing import APIRoute
from pydantic import AfterValidator

from src.main import app
from src.core.path_params import OWNER_RE, REPO_NAME_RE


def test_every_owner_and_repo_path_param_is_validated():
    unvalidated = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for field in route.dependant.path_params:
            if field.name in ("owner", "repo") and not any(
                isinstance(m, AfterValidator) for m in get_args(field.field_info.annotation)[1:]
            ):
                unvalidated.append(f"{route.path} ({field.name})")
    assert unvalidated == []


@pytest.mark.parametrize("repo", ["x?", "x#", "a/b", "x%2F", "..", ".", "", "a b", "x" * 101])
def test_repo_pattern_rejects(repo):
    assert REPO_NAME_RE.match(repo) is None


@pytest.mark.parametrize("repo", ["demo", "my.repo", "_x", ".github", "a-b_c.d", "x" * 100])
def test_repo_pattern_accepts(repo):
    assert REPO_NAME_RE.match(repo)


@pytest.mark.parametrize("owner", ["acme?", "a/b", "-acme", "", "x" * 40, "a b"])
def test_owner_pattern_rejects(owner):
    assert OWNER_RE.match(owner) is None


@pytest.mark.parametrize("owner", ["acme", "Octo-Cat", "a", "user_abc", "x" * 39])
def test_owner_pattern_accepts(owner):
    assert OWNER_RE.match(owner)


def test_clear_caches_rejects_bad_repo_before_the_handler_runs(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.core.auth import UserOut, require_auth
    from src.core.db import get_db
    from src.routers.actions_cache import router

    a = FastAPI()
    a.include_router(router)
    a.dependency_overrides[get_db] = lambda: db
    a.dependency_overrides[require_auth] = lambda: UserOut(
        id=1, email="a@example.com", name=None, is_workspace_admin=True
    )
    resp = TestClient(a).post("/me/repos/acme/x%3F/actions-caches/clear", json={"dry_run": True})
    assert resp.status_code == 422
