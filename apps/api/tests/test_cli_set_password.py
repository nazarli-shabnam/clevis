"""The operator `set_password` command."""

from unittest.mock import patch

from sqlalchemy import func

from src.cli import set_password
from src.core.db import AuditLog, User


def _run(db, argv, *passwords):
    # The command opens its own session; hand it the test's so the rows are visible and rolled back.
    with patch("src.core.db.SessionLocal", return_value=db), patch.object(db, "close"), patch(
        "src.cli.set_password.getpass", side_effect=list(passwords)
    ), patch("src.cli.set_password._prepare_env"):
        return set_password.main(["set_password", *argv])


def test_sets_the_password_signs_the_account_out_and_audits_it(db):
    user = User(email="locked-out@example.com", name=None, password_hash="old-hash", is_workspace_admin=True)
    db.add(user)
    db.commit()

    assert _run(db, ["Locked-Out@Example.com"], "a-brand-new-password", "a-brand-new-password") == 0

    db.expire_all()
    stored = db.query(User).filter(func.lower(User.email) == "locked-out@example.com").one()
    assert stored.password_hash != "old-hash" and stored.token_version == 1
    row = db.query(AuditLog).filter(AuditLog.action == "auth.password_reset").one()
    assert row.actor == "locked-out@example.com" and '"via": "operator-cli"' in row.payload


def test_refuses_unknown_accounts_mismatches_and_weak_passwords(db):
    user = User(email="someone@example.com", name=None, password_hash="old-hash", is_workspace_admin=False)
    db.add(user)
    db.commit()

    assert _run(db, ["nobody@example.com"]) == 1
    assert _run(db, ["someone@example.com"], "a-brand-new-password", "different-password-1") == 1
    assert _run(db, ["someone@example.com"], "short", "short") == 1
    assert _run(db, []) == 2
    db.expire_all()
    assert db.query(User).filter(User.email == "someone@example.com").one().password_hash == "old-hash"


def test_prepare_env_reads_empty_secrets_from_the_init_secrets_files(tmp_path, monkeypatch):
    job, auth = tmp_path / "job_secret", tmp_path / "auth_secret"
    job.write_text("job-from-file" + chr(10))
    auth.write_text("a" * 40 + chr(10))
    monkeypatch.setattr(set_password, "_SECRET_FILES", {"JOB_SECRET_KEY": str(job), "AUTH_SECRET": str(auth)})
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/d")
    monkeypatch.setenv("JOB_SECRET_KEY", "")
    monkeypatch.setenv("AUTH_SECRET", "set-in-the-environment-and-longer-than-32")

    set_password._prepare_env()

    import os

    assert os.environ["JOB_SECRET_KEY"] == "job-from-file"  # empty: filled from the file, newline stripped
    assert os.environ["AUTH_SECRET"] == "set-in-the-environment-and-longer-than-32"  # an environment value wins


def test_prepare_env_leaves_a_secret_unset_when_its_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(set_password, "_SECRET_FILES", {"JOB_SECRET_KEY": str(tmp_path / "absent")})
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/d")
    monkeypatch.delenv("JOB_SECRET_KEY", raising=False)

    set_password._prepare_env()  # no file: no crash, the settings module reports the missing variable

    import os

    assert "JOB_SECRET_KEY" not in os.environ
