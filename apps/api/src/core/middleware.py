from starlette.middleware.base import BaseHTTPMiddleware

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
