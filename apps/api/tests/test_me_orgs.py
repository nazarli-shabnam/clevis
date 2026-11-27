"""GET /me/orgs: the org switcher's membership list."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.orgs import router


def _client(db, user=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[require_auth] = lambda: user
        db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def test_me_orgs_requires_auth(db):
    assert _client(db).get("/me/orgs").status_code == 401


def test_me_orgs_lists_each_membership_with_its_role_and_source(db):
    row = User(email="switcher@e.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add(row)
    db.commit()
    user = UserOut(id=row.id, email=row.email, name=None, is_workspace_admin=False)
    for login, role, source in (("alpha", "admin", "github"), ("beta", "member", "github"), ("gamma", "member", "invite")):
        org = org_repo.get_or_create(db, github_login=login)
        org_membership_repo.get_or_create(db, org_id=org.id, user_id=user.id, role=role, source=source)
    org_repo.get_or_create(db, github_login="not-mine")

    resp = _client(db, user).get("/me/orgs")

    assert resp.status_code == 200
    by_login = {o["org_login"]: o for o in resp.json()}
    assert set(by_login) == {"alpha", "beta", "gamma"}
    assert (by_login["alpha"]["role"], by_login["alpha"]["source"]) == ("admin", "github")
    assert (by_login["beta"]["role"], by_login["beta"]["source"]) == ("member", "github")
    assert (by_login["gamma"]["role"], by_login["gamma"]["source"]) == ("member", "invite")
