"""In-memory fixed-window rate limiter for auth endpoints.

Per-process only: state lives in a module-level dict, so a multi-replica deployment
would need a shared store (e.g. Redis) for this to be effective across instances. Fine
for the current single-API-instance deployment; revisit if we ever scale out /auth.
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


def rate_limit(max_requests: int = _DEFAULT_MAX_REQUESTS, window_seconds: int = _DEFAULT_WINDOW_SECONDS):
    """FastAPI dependency factory: 429s once a client IP exceeds max_requests within window_seconds."""

    def _dependency(request: Request) -> None:
        key = _client_key(request)
        now = time.monotonic()
        with _lock:
            count, window_start = _buckets.get(key, (0, now))
            if now - window_start >= window_seconds:
                count, window_start = 0, now
            count += 1
            _buckets[key] = (count, window_start)
        if count > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later",
            )

    return _dependency


def check_account_rate_limit(
    key: str, *, max_requests: int = _DEFAULT_MAX_REQUESTS, window_seconds: int = _DEFAULT_WINDOW_SECONDS
) -> None:
    """Same fixed-window limiter as rate_limit(), but keyed by a caller-supplied identifier
    (e.g. the submitted login email, lowercased) instead of client IP. Closes the gap where
    an attacker spread across many source IPs could brute-force a single account without
    ever tripping the per-IP bucket. Same in-memory/per-process limitation as rate_limit()."""
    now = time.monotonic()
    with _lock:
        count, window_start = _account_buckets.get(key, (0, now))
        if now - window_start >= window_seconds:
            count, window_start = 0, now
        count += 1
        _account_buckets[key] = (count, window_start)
    if count > max_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, please try again later",
        )
