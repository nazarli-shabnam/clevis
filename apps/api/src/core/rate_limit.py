"""In-memory fixed-window rate limiter for auth endpoints.

Per-process only: a multi-replica deployment would need a shared store (e.g. Redis), and each
replica enforces its own limit, so the effective limit is multiplied by the replica count.

Some keys are chosen by the caller (the per-account limiter is keyed by the email in a login
request), so the stores are bounded: expired buckets are dropped as time passes, and the store is
capped at ``_MAX_BUCKETS`` entries by evicting the least recently used one. A flood of unique keys
therefore cannot grow memory without bound. The trade-off is that a flood of more than
``_MAX_BUCKETS`` distinct live keys can evict (and so reset) another key's counter.
"""

import time
from collections import OrderedDict
from threading import Lock

from fastapi import HTTPException, Request, status

_DEFAULT_MAX_REQUESTS = 10
_DEFAULT_WINDOW_SECONDS = 60

# Hard ceiling on live counters per store (roughly 10-15 MB at most), and how often expired ones are
# swept.
_MAX_BUCKETS = 50_000
_PRUNE_INTERVAL_SECONDS = 30.0

_lock = Lock()


class _Buckets:
    """Bounded map of key -> (hits in the current window, window start, window length).

    Not thread-safe by itself: every method is called with ``_lock`` held."""

    def __init__(self, max_size: int = _MAX_BUCKETS) -> None:
        self._max_size = max_size
        # Ordered by recency: a key moves to the end on every hit, so the first key is the least
        # recently used one. OrderedDict (not dict) keeps both moves and evictions O(1).
        self._data: OrderedDict[str, tuple[int, float, int]] = OrderedDict()
        self._last_prune = 0.0

    def hit(self, key: str, window_seconds: int, now: float) -> int:
        """Count one request for ``key`` and return how many it has made in its current window."""
        self._prune_expired(now)
        count, window_start, _ = self._data.get(key, (0, now, window_seconds))
        if now - window_start >= window_seconds:
            count, window_start = 0, now
        count += 1
        self._data[key] = (count, window_start, window_seconds)
        self._data.move_to_end(key)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)
        return count

    def _prune_expired(self, now: float) -> None:
        # A full scan is O(n) and n is capped, so doing it at most once per interval keeps the
        # per-request cost amortised constant.
        if now - self._last_prune < _PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        expired = [key for key, (_, start, window) in self._data.items() if now - start >= window]
        for key in expired:
            del self._data[key]

    def clear(self) -> None:
        self._data.clear()
        self._last_prune = 0.0

    def __len__(self) -> int:
        return len(self._data)


_buckets = _Buckets()
_account_buckets = _Buckets()


def _client_key(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{request.url.path}:{ip}"


def _check_bucket(buckets: _Buckets, key: str, max_requests: int, window_seconds: int) -> None:
    now = time.monotonic()
    with _lock:
        count = buckets.hit(key, window_seconds, now)
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

    Stops an attacker spread across many IPs brute-forcing one account. Every attempt counts, not
    only failed ones, so repeated attempts against a victim's email can also lock that account out
    of logging in for the rest of the window."""
    _check_bucket(_account_buckets, key, max_requests, window_seconds)
