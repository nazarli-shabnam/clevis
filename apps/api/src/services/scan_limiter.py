"""Caps how many scans one tenant (or one user, for personal scans) runs at once.

A scan is hundreds of GitHub calls, so a handful of them from one caller used to be enough to hold most of
the API's DB pool and starve everyone else. Per-process, like the other in-memory limiters: with N API
replicas the effective cap is N times higher.
"""

import threading
from collections import defaultdict
from contextlib import contextmanager

from fastapi import HTTPException

MAX_CONCURRENT_PER_KEY = 3

_lock = threading.Lock()
_active: defaultdict[str, int] = defaultdict(int)


@contextmanager
def scan_slot(key: str):
    """Hold one of `key`'s scan slots for the body, or raise 429 when all are taken."""
    with _lock:
        if _active[key] >= MAX_CONCURRENT_PER_KEY:
            raise HTTPException(
                status_code=429,
                detail="Too many scans are already running for this account. Wait for one to finish and try again.",
            )
        _active[key] += 1
    try:
        yield
    finally:
        with _lock:
            _active[key] -= 1
            if _active[key] <= 0:
                del _active[key]
