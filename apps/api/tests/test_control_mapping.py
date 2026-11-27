"""The check-to-control table (#1277) stays complete and well-formed as checks are added."""

from checks.github_checks import (
    BranchProtectionEnabled,
    CodeScanningCheck,
    DefaultBranchNoForcePushCheck,
    DependabotAlertsCheck,
    OrgMFARequired,
    SecretScanningEnabled,
)
from checks.hygiene_checks import HYGIENE_CHECKS
from checks.identity_checks import identity_checks
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.routers.compliance import router
from src.services import control_mapping


def _registered_check_ids() -> set[str]:
    cls = [OrgMFARequired, BranchProtectionEnabled, SecretScanningEnabled, DependabotAlertsCheck, CodeScanningCheck,
           DefaultBranchNoForcePushCheck, *HYGIENE_CHECKS]
    return {c().metadata.check_id for c in cls} | {c.metadata.check_id for c in identity_checks({})}


def test_every_check_has_an_explicit_row_and_every_row_is_a_real_check():
    assert set(control_mapping.CHECK_CONTROLS) == _registered_check_ids()


def test_every_mapped_control_is_named_under_a_known_framework():
    frameworks = {f["id"] for f in control_mapping.FRAMEWORKS}
    for check_id, by_framework in control_mapping.CHECK_CONTROLS.items():
        assert set(by_framework) <= frameworks, check_id
        for fw, ids in by_framework.items():
            assert len(ids) == len(set(ids)), (check_id, fw)
            for cid in ids:
                assert cid in control_mapping.CONTROL_NAMES[fw], (check_id, fw, cid)


def test_no_control_is_listed_that_no_check_uses():
    used = {(fw, cid) for m in control_mapping.CHECK_CONTROLS.values() for fw, ids in m.items() for cid in ids}
    named = {(fw, cid) for fw, names in control_mapping.CONTROL_NAMES.items() for cid in names}
    assert named == used


def test_controls_for_keeps_framework_order_and_ignores_unknown_checks():
    rows = control_mapping.controls_for("repository_default_branch_protection_enabled")
    assert [r["framework"] for r in rows] == ["soc2", "cis", "iso27001", "iso27001"]
    assert control_mapping.controls_for("repository_license_present") == []
    assert control_mapping.controls_for("nope") == []


def test_the_endpoint_serves_the_table_to_any_signed_in_user():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: UserOut(id=1, email="a@b.c", name=None, is_workspace_admin=False)
    body = TestClient(app).get("/compliance/controls").json()

    assert [f["id"] for f in body["frameworks"]] == ["soc2", "cis", "iso27001"]
    assert body["checks"]["repository_license_present"] == []
    assert {"framework": "soc2", "id": "CC6.1", "name": "Logical access security"} in body["checks"]["organization_members_mfa_required"]
