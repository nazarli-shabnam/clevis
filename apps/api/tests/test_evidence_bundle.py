"""Compliance evidence bundle: contents, range, tenant scoping, caps, formula defence, audit."""

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import AuditLog, OrgMember, RepoCollaborator, ScanResult, User, get_db
from src.repositories import audit_repo, org_membership_repo, org_repo, scan_results_repo
from src.routers.evidence import router
from src.services import evidence_bundle

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def world(db):
    users = {}
    for name in ("admin", "member"):
        u = User(email=f"ev-{name}@e.com", name=None, password_hash=None, is_workspace_admin=False)
        db.add(u)
        users[name] = u
    db.commit()
    for u in users.values():
        db.refresh(u)
    acme = org_repo.get_or_create(db, github_login="acme-ev")
    other = org_repo.get_or_create(db, github_login="globex-ev")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["admin"].id, role="admin")
    org_membership_repo.get_or_create(db, org_id=acme.id, user_id=users["member"].id, role="member")
    return {"users": users, "acme": acme, "other": other}


def _client(db, user):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: UserOut(
        id=user.id, email=user.email, name=None, is_workspace_admin=False
    )
    app.dependency_overrides[get_db] = lambda: db
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def _scan(db, org, score, *, age_days=1, checks=None, owner=None):
    checks = checks or [
        {"id": "repository_license_present", "title": "License", "severity": "low", "status": "pass", "scored": True, "value": {}},
        {"id": "repository_default_branch_protection_enabled", "title": "Branch protection", "severity": "high",
         "status": "fail", "scored": True, "value": {"repos": ["api", "web"]}},
    ]
    scan_results_repo.insert(
        db, owner=owner or org.github_login, score=score, total_checks=len(checks), failed_checks=1,
        checks=checks, tenant_id=org.tenant_id,
    )
    row = db.query(ScanResult).order_by(ScanResult.id.desc()).first()
    row.created_at = NOW - timedelta(days=age_days)
    db.commit()


def _bundle(resp):
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    return z, json.loads(z.read("manifest.json"))


def _rows(z, name):
    return list(csv.DictReader(io.StringIO(z.read(name).decode())))


def test_admin_downloads_a_zip_with_every_section_and_a_manifest(db, world):
    acme = world["acme"]
    _scan(db, acme, 90, age_days=3)
    _scan(db, acme, 50, age_days=1)
    db.add(OrgMember(tenant_id=acme.tenant_id, login="alice", avatar_url="", role="admin", added_at=NOW, two_factor_enabled=True))
    db.add(OrgMember(tenant_id=acme.tenant_id, login="bob", avatar_url="", role="member", added_at=NOW, two_factor_enabled=None))
    db.add(RepoCollaborator(tenant_id=acme.tenant_id, repo="acme-ev/api", login="contractor", permission="push",
                            is_outside_collaborator=True, granted_at=NOW))
    db.add(RepoCollaborator(tenant_id=acme.tenant_id, repo="acme-ev/api", login="alice", permission="admin",
                            is_outside_collaborator=False, granted_at=NOW))
    db.commit()
    audit_repo.write(db, actor="x@e.com", action="token.created", target="acme-ev", payload={"a": 1}, tenant_id=acme.tenant_id)

    resp = _client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    assert resp.headers["cache-control"] == "no-store" and resp.headers["x-evidence-truncated"] == "false"
    assert "clevis-evidence-acme-ev-" in resp.headers["content-disposition"]
    z, manifest = _bundle(resp)
    assert sorted(z.namelist()) == [
        "audit_log.csv", "failed_checks.csv", "manifest.json", "members.csv", "outside_collaborators.csv", "scan_history.csv",
    ]
    assert manifest["org"] == "acme-ev" and manifest["generated_by"] == "ev-admin@e.com"
    assert manifest["clevis_version"] and manifest["truncated"] is False
    assert manifest["files"]["scan_history.csv"] == {"rows": 2, "truncated": False}

    assert [r["score"] for r in _rows(z, "scan_history.csv")] == ["50", "90"]  # newest first
    failed = _rows(z, "failed_checks.csv")
    assert {(r["check_id"], r["repos"]) for r in failed} == {("repository_default_branch_protection_enabled", "api; web")}
    members = {r["login"]: r for r in _rows(z, "members.csv")}
    assert members["alice"]["two_factor_enabled"] == "true" and members["bob"]["two_factor_enabled"] == ""
    assert [r["login"] for r in _rows(z, "outside_collaborators.csv")] == ["contractor"]
    assert any(r["action"] == "token.created" for r in _rows(z, "audit_log.csv"))


def test_range_limits_scans_and_audit_rows(db, world):
    acme = world["acme"]
    _scan(db, acme, 90, age_days=30)
    _scan(db, acme, 50, age_days=2)
    since = (NOW - timedelta(days=5)).date().isoformat()
    resp = _client(db, world["users"]["admin"]).get(f"/orgs/acme-ev/evidence?since={since}")
    z, manifest = _bundle(resp)
    assert [r["score"] for r in _rows(z, "scan_history.csv")] == ["50"]
    assert manifest["range"]["since"].startswith(since)
    assert _client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence?since=2026-02-01&until=2026-01-01").status_code == 422


def test_it_contains_only_this_orgs_data(db, world):
    _scan(db, world["acme"], 90)
    _scan(db, world["other"], 12)
    db.add(OrgMember(tenant_id=world["other"].tenant_id, login="stranger", avatar_url="", role="member", added_at=NOW))
    db.commit()
    audit_repo.write(db, actor="y@e.com", action="other.secret", target="globex-ev", payload={}, tenant_id=world["other"].tenant_id)
    z, _ = _bundle(_client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence"))
    blob = b"".join(z.read(n) for n in z.namelist())
    assert b"stranger" not in blob and b"other.secret" not in blob
    assert [r["score"] for r in _rows(z, "scan_history.csv")] == ["90"]


def test_only_org_admins_can_export(db, world):
    assert _client(db, world["users"]["member"]).get("/orgs/acme-ev/evidence").status_code == 403


def test_the_export_is_audited_after_the_slice_is_taken(db, world):
    resp = _client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence")
    z, _ = _bundle(resp)
    assert not any(r["action"] == "evidence.export" for r in _rows(z, "audit_log.csv"))
    row = db.query(AuditLog).filter(AuditLog.action == "evidence.export").one()
    assert row.actor == "ev-admin@e.com" and row.tenant_id == world["acme"].tenant_id
    assert json.loads(row.payload)["rows"]["scan_history.csv"] == 0


def test_spreadsheet_formulas_in_cells_are_defanged(db, world):
    acme = world["acme"]
    audit_repo.write(db, actor="=HYPERLINK(\"http://evil\")", action="@cmd", target="+1+1", payload={}, tenant_id=acme.tenant_id)
    z, _ = _bundle(_client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence"))
    row = next(r for r in _rows(z, "audit_log.csv") if r["action"].endswith("cmd"))
    assert row["actor"].startswith("'=") and row["action"] == "'@cmd" and row["target"] == "'+1+1"
    assert evidence_bundle.csv_cell("-12.5") == "-12.5"  # plain numbers are left alone
    assert evidence_bundle.csv_cell(True) == "true" and evidence_bundle.csv_cell(None) == ""


def test_lists_are_capped_and_flagged_truncated(db, world, monkeypatch):
    acme = world["acme"]
    monkeypatch.setattr(evidence_bundle, "SCAN_LIMIT", 2)
    monkeypatch.setattr(evidence_bundle, "ROSTER_LIMIT", 1)
    for i in range(3):
        _scan(db, acme, 60 + i, age_days=i + 1)
    for login in ("a", "b"):
        db.add(OrgMember(tenant_id=acme.tenant_id, login=login, avatar_url="", role="member", added_at=NOW))
    db.commit()
    resp = _client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence")
    z, manifest = _bundle(resp)
    assert resp.headers["x-evidence-truncated"] == "true" and manifest["truncated"] is True
    assert manifest["files"]["scan_history.csv"] == {"rows": 2, "truncated": True}
    assert manifest["files"]["members.csv"] == {"rows": 1, "truncated": True}
    assert len(_rows(z, "scan_history.csv")) == 2


def test_waived_checks_are_reported_with_their_waiver(db, world):
    waived = {"id": "repository_license_present", "title": "License", "severity": "low", "status": "fail", "scored": False,
              "value": {}, "waived": {"until": "2030-01-01T00:00:00+00:00", "owner": "platform", "reason": "legacy"}}
    _scan(db, world["acme"], 100, checks=[waived])
    z, _ = _bundle(_client(db, world["users"]["admin"]).get("/orgs/acme-ev/evidence"))
    row = _rows(z, "failed_checks.csv")[0]
    assert (row["scored"], row["waived_owner"], row["waived_reason"]) == ("false", "platform", "legacy")
