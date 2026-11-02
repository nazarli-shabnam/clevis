"""Tests for the in-memory fixed-window rate limiter."""

from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.core import rate_limit as rate_limit_module
from src.core.rate_limit import _Buckets, check_account_rate_limit, rate_limit


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
    # Past the window the bucket resets, so this must succeed.
    with patch("src.core.rate_limit.time.monotonic", return_value=61.0):
        check_account_rate_limit("acct-window", max_requests=1, window_seconds=60)


# ── bounded memory ───────────────────────────────────────────────────────────

def test_expired_buckets_are_pruned():
    buckets = _Buckets()
    for i in range(100):
        buckets.hit(f"login:{i}@example.com", 60, now=0.0)
    assert len(buckets) == 100

    # Past both the window and the prune interval, the next hit sweeps every expired bucket.
    buckets.hit("login:fresh@example.com", 60, now=100.0)

    assert len(buckets) == 1


def test_prune_keeps_live_buckets_with_longer_windows():
    buckets = _Buckets()
    buckets.hit("short", 60, now=0.0)
    buckets.hit("long", 600, now=0.0)

    buckets.hit("trigger", 60, now=100.0)

    assert buckets.hit("long", 600, now=100.0) == 2  # still counting its original window
    assert buckets.hit("short", 60, now=100.0) == 1  # expired, so it restarted


def test_store_is_capped_by_evicting_the_least_recently_used_key():
    buckets = _Buckets(max_size=3)
    for key in ("a", "b", "c"):
        buckets.hit(key, 60, now=0.0)
    buckets.hit("a", 60, now=1.0)  # "a" is now the most recently used

    buckets.hit("d", 60, now=2.0)

    assert len(buckets) == 3
    assert buckets.hit("a", 60, now=3.0) == 3  # kept its count
    assert buckets.hit("b", 60, now=3.0) == 1  # evicted, so it started over


def test_unique_account_keys_cannot_grow_the_store_past_its_cap():
    with patch.object(rate_limit_module._account_buckets, "_max_size", 5):
        rate_limit_module._account_buckets.clear()
        try:
            for i in range(50):
                check_account_rate_limit(f"login:flood-{i}@example.com")
            assert len(rate_limit_module._account_buckets) == 5
        finally:
            rate_limit_module._account_buckets.clear()


def test_limit_still_applies_to_a_key_while_other_keys_expire():
    with patch("src.core.rate_limit.time.monotonic", return_value=0.0):
        for i in range(20):
            check_account_rate_limit(f"acct-other-{i}", max_requests=1)
        check_account_rate_limit("acct-victim", max_requests=1)
    with patch("src.core.rate_limit.time.monotonic", return_value=45.0):
        # The sweep runs here; the victim's bucket is still inside its window.
        with pytest.raises(HTTPException) as exc_info:
            check_account_rate_limit("acct-victim", max_requests=1)
    assert exc_info.value.status_code == 429
