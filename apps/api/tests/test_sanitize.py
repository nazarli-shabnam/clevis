"""Redaction and truncation of Job.result error text (src.core._sanitize)."""

import pytest

from src.core._sanitize import _MAX_ERROR_LENGTH, _TRUNCATION_SUFFIX, sanitize_error


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "gho_" + "b" * 36,
        "ghs_" + "c" * 36,
        "github_pat_" + "d" * 22 + "_" + "e" * 30,
        "Bearer abc123def",
        "bearer abc123def",
        "Authorization: xyz123",
        "Authorization: Bearer xyz123",
        "authorization: xyz",
    ],
)
def test_sanitize_error_redacts_token_shaped_text(secret):
    out = sanitize_error(f"GitHub said no: {secret} (rate limited)")
    assert "[redacted]" in out
    assert secret.split()[-1] not in out
    assert out.startswith("GitHub said no: ")


def test_sanitize_error_leaves_plain_text_alone():
    assert sanitize_error("Not Found") == "Not Found"


def test_sanitize_error_truncates_to_the_limit_with_a_suffix():
    out = sanitize_error("x" * 2000)
    assert len(out) == _MAX_ERROR_LENGTH
    assert out.endswith(_TRUNCATION_SUFFIX)
    assert sanitize_error("x" * _MAX_ERROR_LENGTH) == "x" * _MAX_ERROR_LENGTH


def test_sanitize_error_redacts_before_truncating_so_a_token_at_the_cut_does_not_leak():
    token = "ghp_" + "z" * 36
    # The token straddles the 500-char cut: redaction must run on the full text first.
    out = sanitize_error("y" * (_MAX_ERROR_LENGTH - 20) + token)
    assert "ghp_" not in out
    assert "zzzzz" not in out
