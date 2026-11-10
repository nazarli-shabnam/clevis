"""Identity checks built from stored data (#815): no GitHub calls, informational by default."""
from unittest.mock import patch

from checks.identity_checks import MembersWithout2FA, OutsideCollaboratorsWithAdmin, identity_checks
from checks.runner import run_all_checks


def _member(login, two_factor):
    return {"login": login, "two_factor_enabled": two_factor}


def _collab(repo, login, permission, outside=True):
    return {"repo": repo, "login": login, "permission": permission, "is_outside_collaborator": outside}


def _run(check):
    return check.run(owner="acme", token="t")


# --- members without 2FA ---

def test_fails_when_any_member_has_no_2fa():
    result = _run(MembersWithout2FA([_member("a", True), _member("b", False), _member("c", True)]))
    assert result == {"status": "fail", "value": {"enabled": 2, "total": 3, "unknown": 0}}


def test_passes_when_every_member_has_2fa():
    result = _run(MembersWithout2FA([_member("a", True), _member("b", True)]))
    assert result["status"] == "pass" and result["value"]["unknown"] == 0


def test_unknown_members_do_not_fail_it_but_are_reported():
    result = _run(MembersWithout2FA([_member("a", True), _member("b", None)]))
    assert result == {"status": "pass", "value": {"enabled": 1, "total": 2, "unknown": 1}}


def test_a_known_failure_still_fails_alongside_unknown_members():
    assert _run(MembersWithout2FA([_member("a", False), _member("b", None)]))["status"] == "fail"


def test_not_applicable_without_a_roster_or_when_2fa_status_was_never_available():
    assert _run(MembersWithout2FA([]))["status"] == "not_applicable"
    assert _run(MembersWithout2FA([_member("a", None), _member("b", None)]))["status"] == "not_applicable"


# --- outside collaborators with admin ---

def test_fails_when_an_outside_collaborator_has_admin():
    result = _run(OutsideCollaboratorsWithAdmin([
        _collab("acme/api", "contractor", "admin"),
        _collab("acme/api", "other", "write"),
        _collab("acme/web", "member-with-admin", "admin", outside=False),  # a member: not an outside collaborator
    ]))
    assert result == {"status": "fail", "value": {"checked": 2, "missing": 1}}


def test_passes_when_outside_collaborators_have_less_than_admin():
    result = _run(OutsideCollaboratorsWithAdmin([_collab("acme/api", "c", "write"), _collab("acme/api", "d", "read")]))
    assert result == {"status": "pass", "value": {"checked": 2, "missing": 0}}


def test_members_only_data_has_no_outside_collaborators_to_flag():
    result = _run(OutsideCollaboratorsWithAdmin([_collab("acme/api", "m", "admin", outside=False)]))
    assert result["status"] == "pass"


def test_collaborators_not_yet_classified_are_not_counted():
    # is_outside_collaborator is NULL until the reconcile fills it in
    assert _run(OutsideCollaboratorsWithAdmin([_collab("acme/api", "c", "admin", outside=None)]))["status"] == "pass"


def test_not_applicable_when_no_collaborator_data_has_been_recorded():
    assert _run(OutsideCollaboratorsWithAdmin([]))["status"] == "not_applicable"


# --- metadata and the runner ---

def test_both_are_informational_and_need_no_repo_list():
    checks = identity_checks({"members": [], "collaborators": []})
    assert [c.metadata.informational for c in checks] == [True, True]
    assert [c.requires_repos for c in checks] == [False, False]
    assert len({c.metadata.check_id for c in checks}) == 2


def test_run_all_checks_adds_them_only_when_stored_data_is_passed():
    with patch("checks.runner._get_all_pages", return_value=[]), patch("checks.github_checks._get", return_value={}):
        without = run_all_checks(owner="acme", token="t", include_hygiene=False)
        with_data = run_all_checks(
            owner="acme", token="t", include_hygiene=False,
            stored_identity={"members": [_member("a", False)], "collaborators": []},
        )

    ids = {c["id"] for c in with_data["checks"]}
    assert len(with_data["checks"]) == len(without["checks"]) + 2
    assert {"organization_members_2fa_enabled", "repository_no_outside_collaborator_admins"} <= ids
    assert "organization_members_2fa_enabled" not in {c["id"] for c in without["checks"]}
    twofa = next(c for c in with_data["checks"] if c["id"] == "organization_members_2fa_enabled")
    assert (twofa["status"], twofa["informational"]) == ("fail", True)


def test_a_failed_repo_prefetch_does_not_break_the_identity_checks():
    with patch("checks.runner._get_all_pages", side_effect=RuntimeError("boom")), patch("checks.github_checks._get", return_value={}):
        result = run_all_checks(
            owner="acme", token="t", include_hygiene=False,
            stored_identity={"members": [_member("a", True)], "collaborators": []},
        )

    twofa = next(c for c in result["checks"] if c["id"] == "organization_members_2fa_enabled")
    assert twofa["status"] == "pass"
