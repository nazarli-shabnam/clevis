import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"

# Marks the handler setup_logging() installs, so calling it again doesn't stack a second one.
_HANDLER_ATTR = "_clevis_handler"

# Libraries that log every call at INFO. The API makes hundreds of GitHub requests per scan, and
# httpx logs one line per request, which would drown the application's own messages.
_QUIET_LOGGERS = ("httpx", "httpcore")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        return True


_FILTER = _RequestIdFilter()


def setup_logging() -> None:
    """Make application logs visible and tag each line with the request id.

    uvicorn only configures its own loggers, so without a root handler the application's
    ``logger.info(...)`` calls are dropped and warnings print with no format -- no request id to
    correlate with the ``X-Request-ID`` response header. Idempotent."""
    root = logging.getLogger()
    if not any(getattr(handler, _HANDLER_ATTR, False) for handler in root.handlers):
        handler = logging.StreamHandler()
        setattr(handler, _HANDLER_ATTR, True)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        # On the handler, not the logger: a logger's filters don't see records propagated from
        # child loggers, and every record this handler formats needs a request_id attribute.
        handler.addFilter(_FILTER)
        root.addHandler(handler)
    # Only ever make the root logger more verbose: a level already set to DEBUG is left alone.
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # uvicorn's own loggers keep their handlers and formats; the filter just makes sure a record
    # carries request_id if one of them is ever given a format that prints it.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for uvicorn_handler in logging.getLogger(name).handlers:
            uvicorn_handler.addFilter(_FILTER)
