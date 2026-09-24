"""Lazily constructed process-wide Redis client for the webhook ingestion queue.

Lazy so test runs that never touch Redis don't need it reachable.
"""

import redis

from src.core.config import settings

_client: redis.Redis | None = None


# redis-py defaults to no timeouts, and xadd() runs synchronously inside an async route, so a hung
# Redis would stall the event loop. No retries: queue_failed is the retry path, and a retried XADD
# could duplicate.
_SOCKET_CONNECT_TIMEOUT_SECONDS = 2
_SOCKET_TIMEOUT_SECONDS = 2


def get_redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_SOCKET_TIMEOUT_SECONDS,
        )
    return _client
