"""setup_logging(): application logs are emitted, and carry the request id."""

import io
import logging
import re

import pytest

from src.core.logging import request_id_var, setup_logging


class _Capture:
    """Runs setup_logging() and sends what its handler writes to a buffer.

    The handler is pointed at the buffer after setup rather than by patching sys.stderr: pytest
    swaps sys.stderr between fixture setup and the test body, so a patch there would be lost."""

    def __init__(self) -> None:
        self.stream = io.StringIO()

    def setup(self) -> None:
        setup_logging()
        for handler in logging.getLogger().handlers:
            if getattr(handler, "_clevis_handler", False):
                handler.setStream(self.stream)

    def getvalue(self) -> str:
        return self.stream.getvalue()


@pytest.fixture()
def clean_logging():
    """Run against an empty root logger (like uvicorn leaves it) and put everything back after."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_quiet = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    for handler in saved_handlers:
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
    yield _Capture()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)
    for name, level in saved_quiet.items():
        logging.getLogger(name).setLevel(level)


def test_info_logs_from_application_loggers_are_emitted(clean_logging):
    clean_logging.setup()

    logging.getLogger("src.routers.webhooks").info("webhook queued")

    assert "webhook queued" in clean_logging.getvalue()


def test_each_line_carries_the_request_id(clean_logging):
    clean_logging.setup()
    token = request_id_var.set("req-abc-123")
    try:
        logging.getLogger("src.routers.webhooks").warning("signature mismatch")
    finally:
        request_id_var.reset(token)

    line = clean_logging.getvalue().strip()
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING \[req-abc-123\] src\.routers\.webhooks: signature mismatch$",
        line,
    )


def test_lines_outside_a_request_show_a_placeholder(clean_logging):
    clean_logging.setup()

    logging.getLogger("src.services.gap_heal_loop").info("sweep tick")

    assert "[-] src.services.gap_heal_loop: sweep tick" in clean_logging.getvalue()


def test_records_from_child_loggers_get_a_request_id_too(clean_logging):
    """The filter is on the handler, so propagated records from any library logger are tagged."""
    clean_logging.setup()
    token = request_id_var.set("req-xyz")
    try:
        logging.getLogger("some.library.deep.logger").warning("library warning")
    finally:
        request_id_var.reset(token)

    assert "[req-xyz] some.library.deep.logger: library warning" in clean_logging.getvalue()


def test_calling_setup_twice_installs_one_handler(clean_logging):
    clean_logging.setup()
    clean_logging.setup()

    logging.getLogger("src.x").info("once")

    assert clean_logging.getvalue().count("once") == 1
    assert sum(getattr(h, "_clevis_handler", False) for h in logging.getLogger().handlers) == 1


def test_per_request_http_client_logs_are_quieted(clean_logging):
    clean_logging.setup()

    logging.getLogger("httpx").info('HTTP Request: GET https://api.github.com/orgs/acme "200 OK"')
    logging.getLogger("httpx").warning("httpx warning still shown")

    output = clean_logging.getvalue()
    assert "HTTP Request" not in output
    assert "httpx warning still shown" in output


def test_a_more_verbose_root_level_is_kept(clean_logging):
    logging.getLogger().setLevel(logging.DEBUG)

    clean_logging.setup()

    assert logging.getLogger().level == logging.DEBUG


def test_a_log_line_written_during_a_request_matches_the_response_header(clean_logging):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.core.middleware import RequestIdMiddleware

    clean_logging.setup()
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/work")
    def work():  # a sync route runs in the threadpool; the request id must follow it there
        logging.getLogger("src.routers.work").info("did the work")
        return {"ok": True}

    response = TestClient(app).get("/work", headers={"X-Request-ID": "trace-42"})

    assert response.headers["X-Request-ID"] == "trace-42"
    assert "[trace-42] src.routers.work: did the work" in clean_logging.getvalue()
