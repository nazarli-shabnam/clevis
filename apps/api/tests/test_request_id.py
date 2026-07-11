"""Tests for X-Request-ID validation and middleware propagation."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.middleware import RequestIdMiddleware
from src.core.request_id import resolve_request_id


def test_resolve_request_id_generates_uuid_when_missing():
    value = resolve_request_id(None)
    uuid.UUID(value)


def test_resolve_request_id_accepts_valid_client_id():
    client_id = "req-abc123_456.test"
    assert resolve_request_id(client_id) == client_id


def test_resolve_request_id_rejects_oversized_value():
    oversized = "a" * 129
    generated = resolve_request_id(oversized)
    assert generated != oversized
    uuid.UUID(generated)


def test_middleware_echoes_sanitized_request_id():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/ping", headers={"X-Request-ID": "x" * 200})
    echoed = response.headers["X-Request-ID"]
    assert echoed != "x" * 200
    uuid.UUID(echoed)
