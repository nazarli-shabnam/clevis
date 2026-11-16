"""GitHub rate-limit classification and pacing shared by the worker's GitHub readers (event backfill,
security-alert backfill, membership reconcile), so the copies cannot drift apart. The API and checks package
carry their own equivalent (``checks.github_checks.retry_delay``); the worker does not import ``apps/api``."""

import time

import httpx

# Cap on a server-supplied Retry-After / X-RateLimit-Reset so a malformed or huge value can't stall a job.
MAX_RETRY_AFTER_SECONDS = 60


def has_rate_limit_headers(resp: httpx.Response) -> bool:
    """A 403 that carries a wait hint (Retry-After, or an exhausted X-RateLimit-Remaining): a rate limit
    that can be slept on. A genuine permission-denied 403 has neither."""
    return resp.status_code == 403 and ("Retry-After" in resp.headers or resp.headers.get("X-RateLimit-Remaining") == "0")


def is_secondary_rate_limit(resp: httpx.Response) -> bool:
    """A secondary rate limit, even the variant GitHub sends as a 403 with neither header, which only its
    message tells from a permission refusal. That variant has no wait hint, so it is not slept on in-line:
    it must surface (not be skipped as a permission error) so the job is requeued with its own backoff."""
    return has_rate_limit_headers(resp) or (resp.status_code == 403 and "secondary rate limit" in resp.text.lower())


def retry_delay_seconds(resp: httpx.Response, attempt: int) -> float:
    """Prefer Retry-After, then X-RateLimit-Reset, both capped at MAX_RETRY_AFTER_SECONDS.

    GitHub sends the rate-limit headers on every response, so a 5xx carries a reset time too; that must not
    pace its retry (it would wait up to the cap instead of backing off). With no header, a rate-limit
    429/403 waits the full cap; other statuses (5xx) back off exponentially."""
    raw = resp.headers.get("Retry-After")
    if raw is not None:
        try:
            return min(float(raw), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    limited = resp.status_code == 429 or has_rate_limit_headers(resp)
    reset_raw = resp.headers.get("X-RateLimit-Reset") if limited else None
    if reset_raw is not None:
        try:
            delay = float(reset_raw) - time.time()
            if delay > 0:
                return min(delay, MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    if limited:
        return MAX_RETRY_AFTER_SECONDS
    return 2**attempt
