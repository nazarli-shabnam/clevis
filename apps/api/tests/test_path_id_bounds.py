"""Out-of-range integer path ids and cursors are 422, not a 500.

The backing columns are 32-bit ``INTEGER``, so an id above ``2**31 - 1`` would reach Postgres as an
out-of-range comparison and fail the request with an unhandled ``DataError``. ``GET /audit`` was
guarded before (``le=2_147_483_647``); these are the rest of the same shape.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.api_tokens import router as api_tokens_router
from src.routers.installations import router as installations_router
from src.routers.jobs import router as jobs_router
from src.routers.notifications import router as notifications_router
from src.routers.org_activity import router as org_activity_router
from src.routers.org_memberships import router as org_memberships_router
from src.routers.waivers import router as waivers_router

BIG = 2_147_483_648


@pytest.fixture()
def world(db):
    user = User(
        email="ids@test.local", name=None, password_hash=None, is_workspace_admin=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # The org routes' membership dependency runs before the endpoint's path-param validation, so
    # the caller must be an admin of a connected org for a 422 (not a 404/403) to come back.
    acme = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=user.id, role="admin")
    return {"user": user}


def _client(db, user, *routers_with_prefix):
    app = FastAPI()
    for router, prefix in routers_with_prefix:
        app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    return TestClient(app)


def test_a_path_id_above_the_database_integer_range_is_422_not_a_500(db, world):
    user = world["user"]
    # (method, path, (router, include prefix)).
    cases = [
        ("get", f"/orgs/acme/audit?before_id={BIG}", (org_activity_router, "")),
        ("get", f"/orgs/acme/jobs?before_id={BIG}", (org_activity_router, "")),
        ("delete", f"/orgs/acme/waivers/{BIG}", (waivers_router, "")),
        ("delete", f"/orgs/acme/api-tokens/{BIG}", (api_tokens_router, "")),
        ("delete", f"/orgs/acme/members/{BIG}", (org_memberships_router, "")),
        (
            "delete",
            f"/orgs/acme/notification-destinations/{BIG}",
            (notifications_router, ""),
        ),
        ("delete", f"/orgs/acme/installations/{BIG}", (installations_router, "")),
        ("get", f"/me/installations/lookup/{BIG}", (installations_router, "")),
        ("delete", f"/me/installations/{BIG}", (installations_router, "")),
        ("get", f"/jobs/{BIG}", (jobs_router, "/jobs")),
    ]
    for method, path, routers in cases:
        client = _client(db, user, routers)
        resp = getattr(client, method)(path)
        assert resp.status_code == 422, f"{method} {path}: {resp.status_code}"
