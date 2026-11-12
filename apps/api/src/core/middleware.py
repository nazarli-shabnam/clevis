from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.auth import SESSION_COOKIE_NAME
from src.core.logging import request_id_var
from src.core.request_id import resolve_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = resolve_request_id(request.headers.get("X-Request-ID"))
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        return response


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class CookieOriginMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests that ride on the session cookie from an untrusted origin.

    With ``SESSION_COOKIE_SAMESITE=none`` a browser attaches the cookie to cross-site requests, and
    CORS only limits who may *read* a response, so a hostile page could still submit a body-less
    POST (logout, revoke sessions, ...). Browsers always send ``Origin`` on cross-site unsafe
    requests, so one that is neither a configured UI origin (``CORS_ORIGINS``) nor this API's own
    origin is refused. Requests without a session cookie or without ``Origin`` (non-browser clients,
    webhooks, API-token CI) are not cookie-borne CSRF and pass through.
    """

    def __init__(self, app, allowed_origins: list[str]):
        super().__init__(app)
        self._allowed = {origin.rstrip("/") for origin in allowed_origins}

    async def dispatch(self, request, call_next):
        origin = request.headers.get("origin")
        if (
            origin is not None
            and request.method not in _SAFE_METHODS
            and request.cookies.get(SESSION_COOKIE_NAME)
            and origin.rstrip("/") not in self._allowed
            and origin != f"{request.url.scheme}://{request.headers.get('host', '')}"
        ):
            return JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        return await call_next(request)
