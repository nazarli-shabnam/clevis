"""URL-path helpers for GitHub REST calls."""
from urllib.parse import quote


def quote_branch(branch: str, safe: str = "") -> str:
    """Percent-encode a branch name for use as a path segment.

    Git allows ``#``, ``?`` and ``%`` in branch names; unquoted they start a fragment/query or a bogus
    escape, so the request goes to a different URL. ``/`` is encoded too (``safe=""``) because the
    ``/branches/{branch}`` endpoints take the whole name as one segment; pass ``safe="/"`` where the name
    is a ref suffix (``git/ref/heads/...``), which keeps its slashes.
    """
    return quote(branch, safe=safe)
