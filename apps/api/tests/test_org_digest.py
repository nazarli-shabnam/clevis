"""Per-org digest controls (#835): the override, recipients, preview and test send, and the sweep honouring them."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo, scan_results_repo
from src.routers.digest import router as digest_router
from src.services import digest_sweep
from src.services.digest_sweep import run_digest_sweep


def _user(db, email, *, verified=True):
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, email_verified=verified)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=None, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(digest_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


@pytest.fixture()
def acme(db):
    admin = _user(db, "digest-admin@e.com")
    other_admin = _user(db, "digest-admin2@e.com")
    member = _user(db, "digest-member@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=other_admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    scan_results_repo.insert(
        db, owner="acme", score=80, total_checks=2, failed_checks=1,
        checks=[{"id": "c", "title": "Broken thing", "severity": "high", "status": "fail"}],
        tenant_id=org.tenant_id,
    )
    return {"org": org, "admin": admin, "other_admin": other_admin, "member": member}


def _instance(cadence):
    return patch("src.services.digest_sweep.get_config", return_value=cadence)


def test_settings_default_to_following_the_instance_and_are_admin_only(db, acme):
    with _instance("off"):
        body = _client(db, acme["admin"]).get("/orgs/acme/digest").json()
        assert _client(db, acme["member"]).get("/orgs/acme/digest").status_code == 403
    assert body["enabled"] is None and body["effective"] is False and body["cadence"] is None
    assert body["recipients"] is None
    assert sorted(body["resolved_recipients"]) == ["digest-admin2@e.com", "digest-admin@e.com"]
    assert "digest-member@e.com" in body["available_recipients"]


def test_an_org_can_opt_in_while_the_instance_is_off_and_opt_out_while_it_is_on(db, acme):
    admin = _client(db, acme["admin"])
    with _instance("off"):
        on = admin.put("/orgs/acme/digest", json={"enabled": True}).json()
    assert (on["effective"], on["cadence"]) == (True, "weekly")  # the gentlest setting, as with scheduled scans
    with _instance("monthly"):
        assert admin.put("/orgs/acme/digest", json={"enabled": False}).json()["effective"] is False
        follow = admin.put("/orgs/acme/digest", json={"enabled": None}).json()
    assert (follow["enabled"], follow["effective"], follow["cadence"]) == (None, True, "monthly")


def test_recipients_must_be_verified_members_and_changes_are_audited(db, acme):
    admin = _client(db, acme["admin"])
    with _instance("weekly"):
        bad = admin.put("/orgs/acme/digest", json={"recipients": ["stranger@example.com"]})
        ok = admin.put("/orgs/acme/digest", json={"recipients": ["Digest-Member@e.com"]})
    assert bad.status_code == 422 and "stranger@example.com" in bad.json()["detail"]
    assert ok.json()["recipients"] == ["digest-member@e.com"]  # normalised
    assert ok.json()["resolved_recipients"] == ["digest-member@e.com"]
    db.execute(text(f"SET app.tenant_id = {int(acme['org'].tenant_id)}"))
    actions = db.execute(
        text("SELECT action FROM audit_logs WHERE tenant_id = :t AND action LIKE 'digest.%'"), {"t": acme["org"].tenant_id}
    ).scalars().all()
    assert actions == ["digest.settings_updated"]  # the rejected change wrote nothing


def test_sweep_sends_to_an_opted_in_org_when_the_instance_is_off_and_only_to_chosen_recipients(db, acme):
    org = acme["org"]
    org.digest_enabled = True
    org.digest_recipients = ["digest-admin2@e.com"]
    db.commit()
    with (
        _instance("off"),
        patch("src.services.digest_sweep.email.is_configured", return_value=True),
        patch("src.services.digest_sweep.email.send_email") as send,
    ):
        run_digest_sweep(db)
    assert [c.args[0] for c in send.call_args_list] == ["digest-admin2@e.com"]


def test_sweep_skips_an_opted_out_org_even_when_the_instance_is_on(db, acme):
    acme["org"].digest_enabled = False
    db.commit()
    with (
        _instance("weekly"),
        patch("src.services.digest_sweep.email.is_configured", return_value=True),
        patch("src.services.digest_sweep.email.send_email") as send,
    ):
        run_digest_sweep(db)
    send.assert_not_called()


def test_a_chosen_recipient_who_is_no_longer_a_verified_member_is_dropped(db, acme):
    acme["org"].digest_recipients = ["digest-member@e.com", "gone@example.com"]
    db.commit()
    assert digest_sweep.recipients_for(db, acme["org"].tenant_id, acme["org"].digest_recipients) == ["digest-member@e.com"]


def test_preview_returns_the_content_without_sending(db, acme):
    with _instance("weekly"), patch("src.services.digest_sweep.email.send_email") as send:
        body = _client(db, acme["admin"]).get("/orgs/acme/digest/preview").json()
    send.assert_not_called()
    assert body["empty"] is False and "acme" in body["subject"] and "Broken thing" in body["body"]


def test_test_send_goes_only_to_the_caller_and_is_audited(db, acme):
    with (
        _instance("off"),
        patch("src.routers.digest.email.is_configured", return_value=True),
        patch("src.routers.digest.email.send_email") as send,
    ):
        resp = _client(db, acme["admin"]).post("/orgs/acme/digest/test")
    assert resp.status_code == 200 and resp.json() == {"sent_to": "digest-admin@e.com"}
    assert [c.args[0] for c in send.call_args_list] == ["digest-admin@e.com"]
    assert send.call_args.args[1].startswith("[Test]")
    db.execute(text(f"SET app.tenant_id = {int(acme['org'].tenant_id)}"))
    assert db.execute(
        text("SELECT count(*) FROM audit_logs WHERE tenant_id = :t AND action = 'digest.test_sent'"),
        {"t": acme["org"].tenant_id},
    ).scalar() == 1


def test_test_send_explains_missing_smtp_and_unverified_address(db, acme):
    with patch("src.routers.digest.email.is_configured", return_value=False):
        assert _client(db, acme["admin"]).post("/orgs/acme/digest/test").status_code == 409
    unverified = _user(db, "unverified-admin@e.com", verified=False)
    org_membership_repo.get_or_create(db, org_id=acme["org"].id, user_id=unverified.id, role="admin")
    with patch("src.routers.digest.email.is_configured", return_value=True):
        resp = _client(db, unverified).post("/orgs/acme/digest/test")
    assert resp.status_code == 409 and "Verify your email" in resp.json()["detail"]
    assert _client(db, acme["member"]).post("/orgs/acme/digest/test").status_code == 403
