"""Tests for the in-memory fixed-window rate limiter."""

from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core.rate_limit import check_account_rate_limit, rate_limit


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


# ── check_account_rate_limit ─────────────────────────────────────────────────

def test_check_account_rate_limit_allows_up_to_max_requests():
    check_account_rate_limit("acct-allow", max_requests=2)
    check_account_rate_limit("acct-allow", max_requests=2)


def test_check_account_rate_limit_blocks_after_max_requests():
    check_account_rate_limit("acct-block", max_requests=2)
    check_account_rate_limit("acct-block", max_requests=2)
    with pytest.raises(HTTPException) as exc_info:
        check_account_rate_limit("acct-block", max_requests=2)
    assert exc_info.value.status_code == 429


def test_check_account_rate_limit_scoped_per_key():
    """Two independently-limited keys don't share a bucket."""
    check_account_rate_limit("acct-a", max_requests=1)
    check_account_rate_limit("acct-b", max_requests=1)
    with pytest.raises(HTTPException):
        check_account_rate_limit("acct-a", max_requests=1)


def test_check_account_rate_limit_resets_after_the_window_elapses():
    with patch("src.core.rate_limit.time.monotonic", return_value=0.0):
        check_account_rate_limit("acct-window", max_requests=1, window_seconds=60)
    # Still within the window -- a second call already exceeds max_requests=1.
    with patch("src.core.rate_limit.time.monotonic", return_value=30.0):
        with pytest.raises(HTTPException):
            check_account_rate_limit("acct-window", max_requests=1, window_seconds=60)
    # Past the window -- the bucket resets instead of continuing to accumulate,
    # so this must succeed rather than 429 again.
    with patch("src.core.rate_limit.time.monotonic", return_value=61.0):
        check_account_rate_limit("acct-window", max_requests=1, window_seconds=60)
