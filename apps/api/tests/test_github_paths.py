from src.routers.security import _force_push_allowed, _repo_row
from src.services.github_paths import quote_branch

ODD = "fix#1?x%20/y"


def test_quote_branch_encodes_reserved_characters_and_slashes():
    assert quote_branch(ODD) == "fix%231%3Fx%2520%2Fy"
    assert quote_branch("feature/x", safe="/") == "feature/x"


class _Recorder:
    """Records every path requested; answers 404 for the branch so the row stays simple."""

    def __init__(self):
        self.paths: list[str] = []

    def request(self, method, path, **_kw):
        self.paths.append(path)
        if path.endswith("/protection"):
            return {"allow_force_pushes": {"enabled": True}}
        return {"protected": True}

    def request_paginated(self, path, **_kw):
        self.paths.append(path)
        return []


def test_security_matrix_requests_quote_the_default_branch():
    rec = _Recorder()
    _repo_row(rec, "acme", {"name": "api", "default_branch": ODD})
    assert rec.paths, "no GitHub calls were made"
    assert all("#" not in p and "?" not in p and " " not in p for p in rec.paths), rec.paths
    assert "/repos/acme/api/branches/fix%231%3Fx%2520%2Fy" in rec.paths


def test_force_push_lookup_quotes_both_protection_and_rules_paths():
    rec = _Recorder()
    assert _force_push_allowed(rec, "acme", "api", ODD) is True
    assert rec.paths == [
        "/repos/acme/api/branches/fix%231%3Fx%2520%2Fy/protection",
        "/repos/acme/api/rules/branches/fix%231%3Fx%2520%2Fy",
    ]
