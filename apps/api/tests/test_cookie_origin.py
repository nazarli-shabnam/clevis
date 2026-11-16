import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.core.auth import SESSION_COOKIE_NAME
from src.core.middleware import CookieOriginMiddleware

UI = "https://ui.example.com"


@pytest.fixture()
def client():
    app = FastAPI()
    app.add_middleware(CookieOriginMiddleware, allowed_origins=[UI])

    @app.post("/act")
    def act():
        return {"ok": True}

    @app.get("/read")
    def read():
        return {"ok": True}

    return TestClient(app, base_url="https://api.example.com")


def _post(client, origin=None, cookie=True, headers=None):
    h = dict(headers or {})
    if origin is not None:
        h["Origin"] = origin
    cookies = {SESSION_COOKIE_NAME: "tok"} if cookie else {}
    return client.post("/act", headers=h, cookies=cookies)


def test_cross_site_cookie_post_is_refused(client):
    assert _post(client, "https://evil.example").status_code == 403
    assert _post(client, "null").status_code == 403


def test_allowed_and_same_origin_pass(client):
    assert _post(client, UI).status_code == 200
    assert _post(client, UI + "/").status_code == 200
    assert _post(client, "https://api.example.com").status_code == 200


def test_requests_that_are_not_cookie_borne_csrf_pass(client):
    assert _post(client).status_code == 200  # no Origin: non-browser client
    assert _post(client, "https://evil.example", cookie=False).status_code == 200
    # A header does not exempt a cookie-bearing request from a foreign origin.
    assert _post(client, "https://evil.example", headers={"Authorization": "Bearer x"}).status_code == 403
    assert _post(client, UI, headers={"Authorization": "Bearer x"}).status_code == 200
    assert client.get("/read", headers={"Origin": "https://evil.example"}, cookies={SESSION_COOKIE_NAME: "t"}).status_code == 200


def test_security_headers_are_added_and_auth_responses_are_not_cached():
    from src.core.middleware import SecurityHeadersMiddleware

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/auth/me")
    def me():
        return {"ok": True}

    @app.get("/auth/own")
    def own():
        return JSONResponse({"ok": True}, headers={"Cache-Control": "private, max-age=5"})

    @app.get("/orgs/x")
    def other():
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/auth/me").headers["Cache-Control"] == "no-store"
    assert c.get("/auth/me").headers["X-Content-Type-Options"] == "nosniff"
    assert c.get("/auth/own").headers["Cache-Control"] == "private, max-age=5"  # a route's own value wins
    other = c.get("/orgs/x")
    assert other.headers["X-Content-Type-Options"] == "nosniff" and "Cache-Control" not in other.headers
