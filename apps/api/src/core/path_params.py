"""Validated ``{owner}`` / ``{repo}`` path segments for the per-repo routes.

Starlette decodes the path before routing, so ``/x%3F/`` reaches a handler as ``x?``. Those values are
later interpolated into GitHub URLs (the API's own calls and the worker's job payloads), where ``?``, ``#``,
``%``, ``/`` or ``..`` would change which URL is called. Anything GitHub itself would not accept as a
login or repo name is rejected with 422 before a handler or job sees it.
"""

import re
from typing import Annotated

from fastapi import Path
from pydantic import AfterValidator

# ``\Z``, not ``$``: ``$`` also matches before a trailing newline, so a name ending in one would pass.
# GitHub logins: alphanumerics, ``-`` and ``_`` (managed-user suffixes); up to 39 chars.
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,38}\Z")
# GitHub repo names: letters, digits, ``.  _  -``; 1-100 chars; never "." or "..".
REPO_NAME_RE = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._-]{1,100}\Z")


def _matching(pattern: re.Pattern[str], what: str):
    def check(value: str) -> str:
        if not pattern.match(value):
            raise ValueError(f"not a valid GitHub {what}")
        return value

    return AfterValidator(check)


Owner = Annotated[str, _matching(OWNER_RE, "owner")]
Repo = Annotated[str, _matching(REPO_NAME_RE, "repository name")]

# Integer row ids in path segments: the backing columns are 32-bit ``INTEGER``, so a larger value
# would reach the database as an out-of-range comparison (an unhandled 500) instead of a clean 404.
RowId = Annotated[int, Path(ge=1, le=2_147_483_647)]

# GitHub logins share Owner's alphabet; used where a login is interpolated into a GitHub URL.
Username = Annotated[str, _matching(OWNER_RE, "username")]
