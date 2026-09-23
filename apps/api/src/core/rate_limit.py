"""In-memory fixed-window rate limiter for auth endpoints.

Per-process only: a multi-replica deployment would need a shared store (e.g. Redis).
"""

import time
from threading import Lock

from fastapi import HTTPException, Request, status

_DEFAULT_MAX_REQUESTS = 10
_DEFAULT_WINDOW_SECONDS = 60

_lock = Lock()
_buckets: dict[str, tuple[int, float]] = {}
_account_buckets: dict[str, tuple[int, float]] = {}


def _client_key(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{request.url.path}:{ip}"


def _check_bucket(buckets: dict[str, tuple[int, float]], key: str, max_requests: int, window_seconds: int) -> None:
    now = time.monotonic()
    with _lock:
        count, window_start = buckets.get(key, (0, now))
        if now - window_start >= window_seconds:
            count, window_start = 0, now
        count += 1
        buckets[key] = (count, window_start)
    if count > max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later",
        )


def rate_limit(max_requests: int = _DEFAULT_MAX_REQUESTS, window_seconds: int = _DEFAULT_WINDOW_SECONDS):
    """FastAPI dependency factory: 429s once a client IP exceeds max_requests within window_seconds."""

    def _dependency(request: Request) -> None:
        _check_bucket(_buckets, _client_key(request), max_requests, window_seconds)

    return _dependency


def check_account_rate_limit(
    key: str, *, max_requests: int = _DEFAULT_MAX_REQUESTS, window_seconds: int = _DEFAULT_WINDOW_SECONDS
) -> None:
    """Same limiter as rate_limit(), keyed by a caller-supplied identifier (e.g. lowercased email).

    Stops an attacker spread across many IPs brute-forcing one account."""
    _check_bucket(_account_buckets, key, max_requests, window_seconds)
