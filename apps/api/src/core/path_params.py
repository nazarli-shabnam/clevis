"""Validated ``{owner}`` / ``{repo}`` path segments for the per-repo routes.

Starlette decodes the path before routing, so ``/x%3F/`` reaches a handler as ``x?``. Those values are
later interpolated into GitHub URLs (the API's own calls and the worker's job payloads), where ``?``, ``#``,
``%``, ``/`` or ``..`` would change which URL is called. Anything GitHub itself would not accept as a
login or repo name is rejected with 422 before a handler or job sees it.
"""

import re
from typing import Annotated

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
