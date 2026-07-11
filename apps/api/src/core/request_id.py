"""Request ID validation for X-Request-ID propagation."""

from __future__ import annotations

import re
import uuid

# Clients may send correlation IDs; cap size and restrict charset so logs
# cannot be polluted with unbounded or crafted values.
_MAX_LEN = 128
_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def resolve_request_id(header_value: str | None) -> str:
    """Return a safe request ID from the client header, or generate a UUID."""
    if header_value is None:
        return str(uuid.uuid4())

    value = header_value.strip()
    if not value or len(value) > _MAX_LEN or not _SAFE_PATTERN.fullmatch(value):
        return str(uuid.uuid4())

    return value
