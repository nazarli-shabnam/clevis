import base64
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from checks import hygiene_checks as h

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def _repo(name="api", **kw):
    return {"name": name, "default_branch": "main", "pushed_at": "2026-10-01T00:00:00Z", "license": {"key": "mit"}, **kw}


def _tree(*paths, truncated=False):
    return {"tree": [{"path": p, "type": "blob"} for p in paths], "truncated": truncated}


def _status_error(code):
    req = httpx.Request("GET", "https://api.github.com/x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


def _run(check, repos, get):
    with patch("checks.github_checks._get", side_effect=get):
        return check.run(owner="acme", token="t", repos=repos)


def test_all_hygiene_checks_are_informational_with_unique_ids():
    metas = [c.metadata for c in h.HYGIENE_CHECKS]
    assert all(m.informational for m in metas)
    assert len({m.check_id for m in metas}) == len(metas) == 5


def test_codeowners_pass_fail_and_empty_repo():
    repos = [_repo("a"), _repo("b"), _repo("empty")]

    def get(url, token):
        if "/a/git/trees" in url:
            return _tree(".github/CODEOWNERS", "README.md")
        if "/b/git/trees" in url:
            return _tree("README.md")
        raise _status_error(409)

    out = _run(h.CodeownersPresent(), repos, get)
    assert out["status"] == "fail"
    assert out["value"] == {"checked": 3, "missing": 1, "unknown": 0, "sampled": 0}


def test_security_policy_pass_and_truncated_tree_is_unknown_not_missing():
    ok = _run(h.SecurityPolicyPresent(), [_repo()], lambda u, t: _tree("SECURITY.md"))
    assert ok["status"] == "pass"
    trunc = _run(h.SecurityPolicyPresent(), [_repo()], lambda u, t: _tree("README.md", truncated=True))
    assert trunc["status"] == "error" and trunc["value"]["unknown"] == 1


def test_license_falls_back_to_the_tree_when_github_detects_none():
    repos = [_repo("a"), _repo("custom", license=None), _repo("bare", license=None)]

    def get(url, token):
        if "/custom/git/trees" in url:
            return _tree("LICENSE")
        if "/bare/git/trees" in url:
            return _tree("README.md")
        raise AssertionError("licensed repo must not hit the API")

    out = _run(h.LicensePresent(), repos, get)
    assert out["status"] == "fail" and out["value"]["missing"] == 1 and out["value"]["checked"] == 3


def test_stale_branches_ignore_default_protected_and_recent_ones():
    branches = [
        {"name": "main", "protected": True, "commit": {"sha": "m"}},
        {"name": "release", "protected": True, "commit": {"sha": "r"}},
        {"name": "old", "protected": False, "commit": {"sha": "o"}},
        {"name": "new", "protected": False, "commit": {"sha": "n"}},
    ]
    dates = {"o": "2026-01-01T00:00:00Z", "n": "2026-09-30T00:00:00Z"}

    def get(url, token):
        if "/branches" in url:
            return branches
        return {"commit": {"committer": {"date": dates[url.rsplit("/", 1)[1]]}}}

    with patch.object(h, "_now", return_value=NOW):
        out = _run(h.StaleBranches(), [_repo()], get)
    assert out["status"] == "fail"
    assert out["value"]["stale_branches"] == 1 and out["value"]["missing"] == 1


def _branch(name, sha=None):
    return {"name": name, "protected": False, "commit": {"sha": sha or name}}


def test_stale_branches_report_sampled_when_more_branches_than_the_lookup_cap():
    branches = [_branch(f"b{i}") for i in range(h._MAX_BRANCH_LOOKUPS + 3)]

    def get(url, token):
        if "/branches" in url:
            return branches
        return {"commit": {"committer": {"date": "2026-09-30T00:00:00Z"}}}

    with patch.object(h, "_now", return_value=NOW):
        out = _run(h.StaleBranches(), [_repo()], get)
    assert out["status"] == "pass" and out["value"]["sampled"] == 1


def test_stale_branches_report_sampled_when_the_first_branch_page_is_full():
    branches = [_branch("main-copy-%d" % i) for i in range(h._BRANCHES_PAGE_SIZE)]
    branches = [{**b, "protected": True} for b in branches]  # nothing to look up, but more may exist

    out = _run(h.StaleBranches(), [_repo()], lambda url, token: branches)
    assert out["value"]["sampled"] == 1


def test_stale_branches_not_sampled_when_every_branch_was_inspected():
    def get(url, token):
        if "/branches" in url:
            return [_branch("a")]
        return {"commit": {"committer": {"date": "2026-09-30T00:00:00Z"}}}

    with patch.object(h, "_now", return_value=NOW):
        out = _run(h.StaleBranches(), [_repo()], get)
    assert out["value"]["sampled"] == 0


def test_stale_branches_unparsable_date_skips_that_branch_not_the_check():
    dates = {"bad": "not-a-date", "old": "2026-01-01T00:00:00Z"}

    def get(url, token):
        if "/branches" in url:
            return [_branch("bad"), _branch("old")]
        return {"commit": {"committer": {"date": dates[url.rsplit("/", 1)[1]]}}}

    with patch.object(h, "_now", return_value=NOW):
        out = _run(h.StaleBranches(), [_repo()], get)
    assert out["status"] == "fail" and out["value"]["stale_branches"] == 1


def test_unpinned_third_party_uses_detection():
    text = """
    steps:
      - uses: actions/checkout@v4
      - uses: ./local-action
      - uses: docker://alpine:3
      - uses: acme/own-action@v1
      - uses: thirdparty/tool@v2
      - uses: 'other/tool@0123456789abcdef0123456789abcdef01234567' # pinned
      - uses: bad/tool@main
    """
    assert h.unpinned_third_party_uses(text, "acme") == ["thirdparty/tool@v2", "bad/tool@main"]


def test_unpinned_actions_check_reads_workflow_files():
    wf = base64.b64encode(b"steps:\n  - uses: thirdparty/tool@v2\n").decode()

    def get(url, token):
        if "/git/trees/" in url:
            return _tree(".github/workflows/ci.yml", "README.md")
        return {"content": wf}

    out = _run(h.UnpinnedActions(), [_repo()], get)
    assert out["status"] == "fail" and out["value"]["unpinned_uses"] == 1


def test_api_errors_make_a_repo_unknown_and_all_unknown_is_error():
    out = _run(h.CodeownersPresent(), [_repo()], lambda u, t: (_ for _ in ()).throw(_status_error(500)))
    assert out["status"] == "error" and out["value"]["unknown"] == 1


def test_not_applicable_without_active_repos_and_sampling_flag():
    assert _run(h.LicensePresent(), [_repo(archived=True)], lambda u, t: {})["status"] == "not_applicable"
    many = [_repo(f"r{i}") for i in range(h._MAX_REPOS + 5)]
    out = _run(h.LicensePresent(), many, lambda u, t: {})
    assert out["value"]["sampled"] == 1 and out["value"]["checked"] == h._MAX_REPOS


def test_tree_is_fetched_once_per_repo_across_checks():
    repo = _repo()
    calls = []

    def get(url, token):
        calls.append(url)
        return _tree("CODEOWNERS", "SECURITY.md")

    with patch("checks.github_checks._get", side_effect=get):
        h.CodeownersPresent().run("acme", "t", repos=[repo])
        h.SecurityPolicyPresent().run("acme", "t", repos=[repo])
    assert len(calls) == 1


def test_tree_404_is_empty_only_without_a_default_branch():
    gone = lambda u, t: (_ for _ in ()).throw(_status_error(404))  # noqa: E731
    failed = _run(h.CodeownersPresent(), [_repo()], gone)
    assert failed["status"] == "error" and failed["value"]["unknown"] == 1
    # No default branch -> nothing to look up, so it's an empty repo and passes.
    empty = _run(h.CodeownersPresent(), [_repo(default_branch=None)], gone)
    assert empty["status"] == "pass" and empty["value"]["unknown"] == 0


def test_unpinned_actions_truncated_tree_is_unknown_unless_a_violation_was_seen():
    wf = base64.b64encode(b"steps:\n  - uses: thirdparty/tool@v2\n").decode()

    def get(url, token):
        if "/git/trees/" in url:
            return _tree("README.md", truncated=True)
        return {"content": wf}

    out = _run(h.UnpinnedActions(), [_repo()], get)
    assert out["status"] == "error" and out["value"]["unknown"] == 1

    def get_with_wf(url, token):
        if "/git/trees/" in url:
            return _tree(".github/workflows/ci.yml", truncated=True)
        return {"content": wf}

    failing = _run(h.UnpinnedActions(), [_repo()], get_with_wf)
    assert failing["status"] == "fail" and failing["value"]["unpinned_uses"] == 1
