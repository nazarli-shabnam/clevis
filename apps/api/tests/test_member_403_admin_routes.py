"""A plain org member (and a signed-in outsider) must get 403 on every admin-gated org route (#1343).

Each route is hit with a member and with an outsider; a downgrade of its `require_org_role("admin")` to
"member" (or its removal) turns the member case into a 2xx/4xx other than 403. The authorization check runs
before any lookup, so the ids and bodies below only need to be well-formed, not real."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.routing import Match

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers import (
    api_tokens,
    automation_schedules,
    digest,
    invitations,
    notifications,
    pr_nudges,
)

_ROUTES = [
    ("POST", "/orgs/acme/repos/acme/widgets/pr-nudges", {"dry_run": True}),
    ("GET", "/orgs/acme/notification-destinations", None),
    ("DELETE", "/orgs/acme/notification-destinations/1", None),
    ("POST", "/orgs/acme/notification-destinations/1/test", None),
    ("PUT", "/orgs/acme/digest", {"enabled": False}),
    ("GET", "/orgs/acme/digest/preview", None),
    ("PUT", "/orgs/acme/automation-schedules", {}),
    ("PUT", "/orgs/acme/repos/acme/widgets/automation/pr-nudge", {"enabled": True}),
    ("DELETE", "/orgs/acme/api-tokens/1", None),
    ("GET", "/orgs/acme/badge", None),
    ("POST", "/orgs/acme/invitations/1/revoke", None),
]


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def acme(db):
    org = org_repo.get_or_create(db, github_login="acme")
    member = _make_user(db, "m403-member@e.com")
    admin = _make_user(db, "m403-admin@e.com")
    outsider = _make_user(db, "m403-outsider@e.com")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    return {"member": member, "admin": admin, "outsider": outsider}


def _client(db, user):
    app = FastAPI()
    for module in (pr_nudges, notifications, digest, automation_schedules, api_tokens, invitations):
        app.include_router(module.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


@pytest.mark.parametrize("who", ["member", "outsider"])
@pytest.mark.parametrize("method,path,body", _ROUTES, ids=[f"{m} {p}" for m, p, _ in _ROUTES])
def test_admin_route_refuses_non_admins_with_403(db, acme, who, method, path, body):
    client = _client(db, acme[who])

    with patch("src.routers.pr_nudges.resolve_org_token") as resolve_token:
        resp = client.request(method, path, json=body)

    assert resp.status_code == 403, resp.text
    # The nudge route posts comments and labels to GitHub as the App: it must not even look up a token.
    resolve_token.assert_not_called()


@pytest.mark.parametrize("method,path,body", _ROUTES, ids=[f"{m} {p}" for m, p, _ in _ROUTES])
def test_admin_gate_lets_an_admin_through(db, acme, method, path, body):
    # Guards the test above against passing for the wrong reason (e.g. a mis-spelled path answering 403/404).
    client = _client(db, acme["admin"])
    scope = {"type": "http", "path": path, "method": method}
    assert any(route.matches(scope)[0] == Match.FULL for route in client.app.routes), f"no route for {method} {path}"

    with patch("src.routers.pr_nudges.resolve_org_token", side_effect=RuntimeError("stop before GitHub")):
        try:
            resp = client.request(method, path, json=body)
        except RuntimeError:
            return  # got past the authorization check and reached the GitHub lookup

    assert resp.status_code != 403, resp.text
