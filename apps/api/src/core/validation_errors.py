"""Redacting handler for request-validation (422) errors.

FastAPI's default 422 body echoes each error's ``input`` -- for a missing or invalid field that is
the *whole* request body, so a body carrying a GitHub token or a password comes straight back to the
caller (and to any proxy or log in between). ``SecretStr`` fields do not help: the raw input is
echoed before the model is built. Clients only need ``loc`` and ``msg`` to show a validation error,
so the input is dropped from every 422.
"""

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [{key: value for key, value in error.items() if key != "input"} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
