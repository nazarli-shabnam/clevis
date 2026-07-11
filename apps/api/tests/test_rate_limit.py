"""Tests for the in-memory fixed-window rate limiter."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.core.rate_limit import rate_limit


def _app(path: str, max_requests: int, window_seconds: int = 60) -> FastAPI:
    # Bucket keys are (path, client IP), so each test uses its own path to avoid
    # sharing rate-limit state with other tests via the module-level bucket dict.
    app = FastAPI()

    @app.get(path, dependencies=[Depends(rate_limit(max_requests=max_requests, window_seconds=window_seconds))])
    def limited():
        return {"ok": True}

    return app


def test_rate_limit_allows_up_to_max_requests():
    client = TestClient(_app("/limited-allow", max_requests=2))
    assert client.get("/limited-allow").status_code == 200
    assert client.get("/limited-allow").status_code == 200


def test_rate_limit_blocks_after_max_requests():
    client = TestClient(_app("/limited-block", max_requests=2))
    assert client.get("/limited-block").status_code == 200
    assert client.get("/limited-block").status_code == 200
    resp = client.get("/limited-block")
    assert resp.status_code == 429


def test_rate_limit_scoped_per_path():
    """Two independently-limited routes don't share a bucket."""
    app = FastAPI()

    @app.get("/a", dependencies=[Depends(rate_limit(max_requests=1))])
    def a():
        return {"ok": True}

    @app.get("/b", dependencies=[Depends(rate_limit(max_requests=1))])
    def b():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/a").status_code == 200
    assert client.get("/b").status_code == 200
    assert client.get("/a").status_code == 429
