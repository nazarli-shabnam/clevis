import re

# Job.result is stored and later exposed verbatim via GET /jobs to workspace admins.
# Cap length and strip anything that looks like a token/credential fragment as defense
# in depth against GitHub API error text echoing request details (URLs, headers, params).
_MAX_ERROR_LENGTH = 500
_TRUNCATION_SUFFIX = "...(truncated)"

_REDACT_PATTERNS = [
    re.compile(r"gh[oprsu]_[A-Za-z0-9]{20,}"),  # GitHub PAT/OAuth/app token prefixes
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"[Bb]earer\s+\S+"),
    re.compile(r"[Aa]uthorization:\s*\S+"),
]


def sanitize_error(text: str) -> str:
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("[redacted]", text)
    if len(text) > _MAX_ERROR_LENGTH:
        text = text[: _MAX_ERROR_LENGTH - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
    return text
