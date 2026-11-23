"""An HTTPException that also carries a stable machine-readable ``code``.

`detail` stays the human message (every client already shows it); `code` lets a client branch without searching
the message text, which can embed user data such as an email address."""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class CodedHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


async def coded_http_exception_handler(_request: Request, exc: CodedHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})
