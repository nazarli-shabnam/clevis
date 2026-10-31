"""Tests for the org invitation create/list/revoke/accept flow."""

import secrets
from datetime import datetime, timedelta, timezone

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.core.auth import UserOut, require_auth
from src.core.db import Invitation, User, get_db
from src.repositories import invitation_repo, org_membership_repo, org_repo
from src.routers.invitations import router as invitations_router


def _make_user(db, email: str, email_verified: bool = True) -> UserOut:
    # Verified by default so email-match tests don't also exercise the verification requirement.
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, email_verified=email_verified)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def acme_org(db):
    admin = _make_user(db, "admin@e.com")
    member = _make_user(db, "member@e.com")
    invitee = _make_user(db, "bob@acme.com")
    wrong_email = _make_user(db, "carol@acme.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=member.id, role="member")
    return {"org": org, "admin": admin, "member": member, "invitee": invitee, "wrong_email": wrong_email}


def _client(db, user):
    app = FastAPI()
    app.include_router(invitations_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


def test_create_invitation_requires_admin(db, acme_org):
    resp = _client(db, acme_org["member"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 403


def test_create_invitation_admin_ok(db, acme_org):
    resp = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["invitation"]["email"] == "bob@acme.com"
    assert body["invitation"]["status"] == "pending"
    assert "/invite/" in body["invite_link"]


def test_create_invitation_sets_tenant_id(db, acme_org):
    from src.core.db import Tenant

    resp = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 200
    token = resp.json()["invite_link"].rsplit("/", 1)[-1]

    invitation = invitation_repo.get_by_token(db, token)

    assert invitation.tenant_id is not None
    tenant = db.query(Tenant).filter(Tenant.id == invitation.tenant_id).first()
    assert tenant.kind == "org"
    assert tenant.org_id == acme_org["org"].id


def test_create_invitation_rejects_duplicate_pending_invite(db, acme_org):
    client = _client(db, acme_org["admin"])
    first = client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert first.status_code == 200

    second = client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert second.status_code == 409

    # Only the first invite exists -- the rejected attempt didn't create a second row.
    listing = client.get("/orgs/acme/invitations").json()
    assert len(listing) == 1


def test_create_invitation_duplicate_check_is_case_insensitive(db, acme_org):
    client = _client(db, acme_org["admin"])
    client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"})

    resp = client.post("/orgs/acme/invitations", json={"email": "BOB@ACME.COM"})
    assert resp.status_code == 409


def test_create_invitation_allows_new_invite_after_the_pending_one_is_revoked(db, acme_org):
    client = _client(db, acme_org["admin"])
    created = client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    client.post(f"/orgs/acme/invitations/{created['invitation']['id']}/revoke")

    resp = client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 200


def test_create_invitation_allows_new_invite_after_the_pending_one_expires(db, acme_org):
    client = _client(db, acme_org["admin"])
    created = client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    _expire_invitation(db, created["invitation"]["id"])

    resp = client.post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    assert resp.status_code == 200


def test_invitation_repo_rejects_a_second_pending_row(db, acme_org):
    # DB-level guard: bypasses the router's pre-check; without the partial unique index this
    # would create a duplicate pending row.
    org, admin = acme_org["org"], acme_org["admin"]
    invitation_repo.create(db, org_id=org.id, email="dup@acme.com", invited_by_user_id=admin.id)

    with pytest.raises(invitation_repo.DuplicatePendingInvitation):
        invitation_repo.create(db, org_id=org.id, email="DUP@ACME.COM", invited_by_user_id=admin.id)

    pending = invitation_repo.list_pending_for_email(db, "dup@acme.com")
    assert len(pending) == 1


def test_partial_unique_index_rejects_a_raw_duplicate_pending_insert(db, acme_org):
    # The constraint itself must reject a second pending row for (org_id, lower(email)); this
    # is what makes concurrent create_invitation requests one-winner.
    org, admin = acme_org["org"], acme_org["admin"]
    invitation_repo.create(db, org_id=org.id, email="race@acme.com", invited_by_user_id=admin.id)

    with pytest.raises(IntegrityError):
        # begin_nested so the failed INSERT rolls back to a savepoint, leaving the
        # first invitation (and acme_org) intact for the assertion below.
        with db.begin_nested():
            db.add(
                Invitation(
                    org_id=org.id,
                    email="RACE@acme.com",  # different case -> same lower(email) index key
                    token=secrets.token_urlsafe(32),
                    status="pending",
                    invited_by_user_id=admin.id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    tenant_id=org.tenant_id,
                )
            )
            db.flush()

    assert len(invitation_repo.list_pending_for_email(db, "race@acme.com")) == 1


def test_create_invitation_reraises_non_duplicate_integrity_errors(db, acme_org):
    # A different constraint failure (bad FK) must surface as itself, not a misleading 409.
    org = acme_org["org"]
    with pytest.raises(IntegrityError):
        invitation_repo.create(db, org_id=org.id, email="fk@acme.com", invited_by_user_id=999_999_999)


def test_list_invitations_admin_only(db, acme_org):
    _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    resp = _client(db, acme_org["admin"]).get("/orgs/acme/invitations")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp = _client(db, acme_org["member"]).get("/orgs/acme/invitations")
    assert resp.status_code == 403


def test_preview_invitation_unauthenticated(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    app = FastAPI()
    app.include_router(invitations_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get(f"/invitations/{token}")
    assert resp.status_code == 200
    # The invitee's email is intentionally not disclosed on this unauthenticated endpoint.
    assert resp.json() == {"org_login": "acme", "status": "pending"}


def test_accept_invitation_wrong_email_forbidden(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    resp = _client(db, acme_org["wrong_email"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 403


def test_accept_invitation_ok(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 200
    assert resp.json() == {"org_login": "acme", "role": "member"}

    # Accepting again fails — invitation is no longer pending.
    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 409


def test_accept_invitation_unverified_email_forbidden(db, acme_org):
    # Email match alone isn't enough for an account that hasn't verified the invited inbox.
    unverified = _make_user(db, "dave@acme.com", email_verified=False)
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "dave@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    resp = _client(db, unverified).post(f"/invitations/{token}/accept")
    assert resp.status_code == 403
    assert "verify" in resp.json()["detail"].lower()


def test_accept_invitation_succeeds_once_verified(db, acme_org):
    unverified = _make_user(db, "erin@acme.com", email_verified=False)
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "erin@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    resp = _client(db, unverified).post(f"/invitations/{token}/accept")
    assert resp.status_code == 403

    user_row = db.query(User).filter(User.id == unverified.id).first()
    user_row.email_verified = True
    db.commit()

    resp = _client(db, unverified).post(f"/invitations/{token}/accept")
    assert resp.status_code == 200
    assert resp.json() == {"org_login": "acme", "role": "member"}


def test_accept_invitation_case_insensitive_email_match(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "Bob@Acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]

    # Invitee's account email differs only in case from the invited address.
    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 200
    assert resp.json() == {"org_login": "acme", "role": "member"}


def test_revoke_invitation(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    invitation_id = created["invitation"]["id"]

    resp = _client(db, acme_org["admin"]).post(f"/orgs/acme/invitations/{invitation_id}/revoke")
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"

    token = created["invite_link"].rsplit("/", 1)[-1]
    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 409


def _expire_invitation(db, invitation_id: int) -> None:
    from src.core.db import Invitation

    invitation = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    invitation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()


def test_create_invitation_sets_expiry(db, acme_org):
    before = datetime.now(timezone.utc)
    resp = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"})
    after = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(resp.json()["invitation"]["expires_at"])
    assert before + timedelta(days=7) <= expires_at <= after + timedelta(days=7)


def test_accept_expired_invitation_returns_410(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]
    _expire_invitation(db, created["invitation"]["id"])

    resp = _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept")
    assert resp.status_code == 410


def test_preview_expired_invitation_shows_expired_status(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]
    _expire_invitation(db, created["invitation"]["id"])

    app = FastAPI()
    app.include_router(invitations_router)
    app.dependency_overrides[get_db] = lambda: db
    resp = TestClient(app).get(f"/invitations/{token}")
    assert resp.status_code == 200
    assert resp.json() == {"org_login": "acme", "status": "expired"}


def test_list_invitations_reflects_expired_status(db, acme_org):
    created = _client(db, acme_org["admin"]).post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    _expire_invitation(db, created["invitation"]["id"])

    resp = _client(db, acme_org["admin"]).get("/orgs/acme/invitations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "expired"
    assert "expires_at" in body[0]


def test_invitation_lifecycle_is_audited(db, acme_org):
    from src.core.db import AuditLog

    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    token = created["invite_link"].rsplit("/", 1)[-1]
    assert _client(db, acme_org["invitee"]).post(f"/invitations/{token}/accept").status_code == 200
    other = admin.post("/orgs/acme/invitations", json={"email": "dave@acme.com"}).json()
    assert admin.post(f"/orgs/acme/invitations/{other['invitation']['id']}/revoke").status_code == 200

    actions = {r.action for r in db.query(AuditLog).filter(AuditLog.target == "acme")}
    assert {"invitation.create", "invitation.accept", "invitation.revoke"} <= actions


def _token(created: dict) -> str:
    return created["invite_link"].rsplit("/", 1)[-1]


def test_regenerate_gives_a_new_link_and_kills_the_old_one(db, acme_org):
    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    inv_id = created["invitation"]["id"]

    resp = admin.post(f"/orgs/acme/invitations/{inv_id}/regenerate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["invitation"]["id"] == inv_id and body["invitation"]["status"] == "pending"
    assert _token(body) != _token(created)
    assert _client(db, acme_org["invitee"]).get(f"/invitations/{_token(created)}").status_code == 404
    assert _client(db, acme_org["invitee"]).post(f"/invitations/{_token(body)}/accept").status_code == 200


def test_regenerate_revives_an_expired_invitation(db, acme_org):
    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    inv_id = created["invitation"]["id"]
    _expire_invitation(db, inv_id)

    body = admin.post(f"/orgs/acme/invitations/{inv_id}/regenerate").json()

    assert body["invitation"]["status"] == "pending"
    assert datetime.fromisoformat(body["invitation"]["expires_at"]) > datetime.now(timezone.utc) + timedelta(days=6)


def test_regenerate_an_expired_invitation_conflicts_with_a_newer_pending_one(db, acme_org):
    admin = _client(db, acme_org["admin"])
    old = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    _expire_invitation(db, old["invitation"]["id"])
    assert admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).status_code == 200

    resp = admin.post(f"/orgs/acme/invitations/{old['invitation']['id']}/regenerate")

    assert resp.status_code == 409


@pytest.mark.parametrize("final", ["revoke", "accept"])
def test_regenerate_refuses_revoked_and_accepted_invitations(db, acme_org, final):
    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    inv_id = created["invitation"]["id"]
    if final == "revoke":
        admin.post(f"/orgs/acme/invitations/{inv_id}/revoke")
    else:
        _client(db, acme_org["invitee"]).post(f"/invitations/{_token(created)}/accept")

    assert admin.post(f"/orgs/acme/invitations/{inv_id}/regenerate").status_code == 409


def test_regenerate_and_resend_require_org_admin_and_the_right_org(db, acme_org):
    admin = _client(db, acme_org["admin"])
    inv_id = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()["invitation"]["id"]
    member = _client(db, acme_org["member"])

    assert member.post(f"/orgs/acme/invitations/{inv_id}/regenerate").status_code == 403
    assert member.post(f"/orgs/acme/invitations/{inv_id}/resend").status_code == 403
    assert admin.post("/orgs/acme/invitations/999999/regenerate").status_code == 404
    other = org_repo.get_or_create(db, github_login="other")
    org_membership_repo.get_or_create(db, org_id=other.id, user_id=acme_org["admin"].id, role="admin")
    # An invitation id from another org must not be reachable through this org's URL.
    assert admin.post(f"/orgs/other/invitations/{inv_id}/regenerate").status_code == 404


def test_resend_without_smtp_is_a_clear_409_and_keeps_the_link(db, acme_org):
    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()

    with patch("src.services.email.is_configured", return_value=False):
        resp = admin.post(f"/orgs/acme/invitations/{created['invitation']['id']}/resend")

    assert resp.status_code == 409 and "SMTP" in resp.json()["detail"]
    assert _client(db, acme_org["invitee"]).get(f"/invitations/{_token(created)}").status_code == 200


def test_resend_emails_a_fresh_link(db, acme_org):
    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()

    with patch("src.services.email.is_configured", return_value=True), patch(
        "src.services.email.send_email"
    ) as send:
        resp = admin.post(f"/orgs/acme/invitations/{created['invitation']['id']}/resend")

    assert resp.status_code == 200 and resp.json()["status"] == "pending"
    to, subject, text = send.call_args.args[:3]
    assert to == "bob@acme.com" and "acme" in subject
    new_link = next(line for line in text.splitlines() if "/invite/" in line)
    new_token = _token({"invite_link": new_link})
    assert new_token != _token(created)
    # The emailed token must be the one saved on the invitation, and the old one must be dead.
    assert _client(db, acme_org["invitee"]).get(f"/invitations/{new_token}").status_code == 200
    assert _client(db, acme_org["invitee"]).get(f"/invitations/{_token(created)}").status_code == 404


def test_resend_reports_a_failed_send_as_502(db, acme_org):
    admin = _client(db, acme_org["admin"])
    created = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()

    with patch("src.services.email.is_configured", return_value=True), patch(
        "src.services.email.send_email", side_effect=OSError("smtp down")
    ):
        resp = admin.post(f"/orgs/acme/invitations/{created['invitation']['id']}/resend")

    assert resp.status_code == 502 and "Regenerate" in resp.json()["detail"]
    # The token was rotated before the send, so the old link is already dead (the 502 says so).
    assert _client(db, acme_org["invitee"]).get(f"/invitations/{_token(created)}").status_code == 404


def test_regenerate_ignores_a_newer_pending_invitation_that_has_lapsed(db, acme_org):
    from src.core.db import Invitation

    admin = _client(db, acme_org["admin"])
    old = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()
    _expire_invitation(db, old["invitation"]["id"])
    newer = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()  # stores `old` as expired
    # `newer` has lapsed too but is still stored as 'pending', which the unique index counts.
    _expire_invitation(db, newer["invitation"]["id"])

    resp = admin.post(f"/orgs/acme/invitations/{old['invitation']['id']}/regenerate")

    assert resp.status_code == 200 and resp.json()["invitation"]["status"] == "pending"
    db.expire_all()
    assert db.get(Invitation, newer["invitation"]["id"]).status == "expired"


def test_bulk_invite_reports_each_address(db, acme_org):
    admin = _client(db, acme_org["admin"])
    admin.post("/orgs/acme/invitations", json={"email": "taken@acme.com"})

    resp = admin.post(
        "/orgs/acme/invitations/bulk",
        json={"emails": ["a@acme.com", "A@ACME.com", "not-an-email", " NOT-AN-EMAIL", "taken@acme.com", " b@acme.com "]},
    )

    assert resp.status_code == 200
    results = {r["email"]: r for r in resp.json()["results"]}
    assert [r["email"] for r in resp.json()["results"]] == ["a@acme.com", "not-an-email", "taken@acme.com", "b@acme.com"]
    assert results["a@acme.com"]["status"] == "created" and "/invite/" in results["a@acme.com"]["invite_link"]
    assert results["b@acme.com"]["status"] == "created"
    assert results["not-an-email"]["status"] == "invalid" and results["not-an-email"]["invite_link"] is None
    assert results["taken@acme.com"]["status"] == "already_pending"
    assert len(admin.get("/orgs/acme/invitations").json()) == 3  # taken + a + b


def test_bulk_invite_is_admin_only_and_capped(db, acme_org):
    member = _client(db, acme_org["member"])
    assert member.post("/orgs/acme/invitations/bulk", json={"emails": ["a@acme.com"]}).status_code == 403
    admin = _client(db, acme_org["admin"])
    assert admin.post("/orgs/acme/invitations/bulk", json={"emails": []}).status_code == 422
    too_many = [f"u{i}@acme.com" for i in range(51)]
    assert admin.post("/orgs/acme/invitations/bulk", json={"emails": too_many}).status_code == 422


def test_bulk_invite_caps_each_address_length(db, acme_org):
    admin = _client(db, acme_org["admin"])

    assert admin.post("/orgs/acme/invitations/bulk", json={"emails": ["a" * 255]}).status_code == 422
    # 254 is the longest a real address can be: it is judged (invalid), not rejected outright, and
    # padding around it doesn't count towards the cap.
    at_limit = admin.post("/orgs/acme/invitations/bulk", json={"emails": [" " + "a" * 254]})
    assert at_limit.status_code == 200 and at_limit.json()["results"][0]["status"] == "invalid"


def test_bulk_invite_reports_an_address_that_lost_a_race_as_already_pending(db, acme_org):
    admin = _client(db, acme_org["admin"])

    # The pre-check saw nothing pending, then a concurrent request's insert won (partial unique index).
    with patch(
        "src.routers.invitations.invitation_repo.create",
        side_effect=invitation_repo.DuplicatePendingInvitation("a@acme.com"),
    ):
        resp = admin.post("/orgs/acme/invitations/bulk", json={"emails": ["a@acme.com"]})

    assert resp.status_code == 200
    assert [(r["email"], r["status"]) for r in resp.json()["results"]] == [("a@acme.com", "already_pending")]


def test_bulk_invite_commits_each_address_with_its_audit_row(db, acme_org):
    from sqlalchemy.exc import SQLAlchemyError

    from src.repositories import audit_repo

    admin = _client(db, acme_org["admin"])
    real_write = audit_repo.write

    def flaky_write(db_, actor, action, target, payload, *args, **kwargs):
        if payload.get("email") == "b@acme.com":
            raise SQLAlchemyError("audit down")
        return real_write(db_, actor, action, target, payload, *args, **kwargs)

    with patch("src.routers.invitations.audit_repo.write", side_effect=flaky_write):
        resp = admin.post("/orgs/acme/invitations/bulk", json={"emails": ["a@acme.com", "b@acme.com", "c@acme.com"]})

    assert resp.status_code == 200
    assert {r["email"]: r["status"] for r in resp.json()["results"]} == {
        "a@acme.com": "created", "b@acme.com": "failed", "c@acme.com": "created",
    }
    # b's invitation went down with its audit row: nothing was left behind without a link.
    assert sorted(i["email"] for i in admin.get("/orgs/acme/invitations").json()) == ["a@acme.com", "c@acme.com"]


def test_reissue_and_bulk_are_audited(db, acme_org):
    from src.core.db import AuditLog

    admin = _client(db, acme_org["admin"])
    inv_id = admin.post("/orgs/acme/invitations", json={"email": "bob@acme.com"}).json()["invitation"]["id"]
    admin.post(f"/orgs/acme/invitations/{inv_id}/regenerate")
    with patch("src.services.email.is_configured", return_value=True), patch("src.services.email.send_email"):
        admin.post(f"/orgs/acme/invitations/{inv_id}/resend")
    admin.post("/orgs/acme/invitations/bulk", json={"emails": ["x@acme.com"]})

    rows = db.query(AuditLog).filter(AuditLog.target == "acme").all()
    assert {"invitation.regenerate", "invitation.resend"} <= {r.action for r in rows}
    assert sum(r.action == "invitation.create" for r in rows) == 2  # bob + x
    assert all("token" not in str(r.payload) for r in rows)
