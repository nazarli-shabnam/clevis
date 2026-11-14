"""Operator fallback to set an account's password when the emailed reset can't be used (no SMTP configured, or
the workspace admin is locked out).

    docker compose exec api python -m src.cli.set_password owner@example.com

It prompts for the new password (twice, never echoed or taken from the command line), applies the same length
rules as sign-up, signs the account out everywhere, and writes an ``auth.password_reset`` audit row with
``via: operator-cli``. Running it needs shell access to the container, which already implies the database
credentials, so it grants nothing beyond what the operator has.
"""

import os
import sys
from getpass import getpass
from urllib.parse import quote


def _prepare_env() -> None:
    """The container entrypoint builds these for the server process only; rebuild them the same way here, before
    the settings module reads them."""
    if not os.environ.get("DATABASE_URL"):
        user, password, name = (os.environ.get(k) for k in ("DB_USER", "DB_PASSWORD", "DB_NAME"))
        if not (user and password and name):
            sys.exit("DATABASE_URL, or DB_USER, DB_PASSWORD and DB_NAME, must be set")
        os.environ["DATABASE_URL"] = f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}@db:5432/{quote(name, safe='')}"
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")  # required by the settings, not used here


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m src.cli.set_password <email>", file=sys.stderr)
        return 2
    _prepare_env()

    from fastapi import HTTPException
    from sqlalchemy import func

    from src.core.db import SessionLocal, User
    from src.repositories import session_repo
    from src.routers.auth import _check_new_password, _hash_password
    from src.services import auth_audit

    email = argv[1].strip()
    with SessionLocal() as db:
        user = db.query(User).filter(func.lower(User.email) == email.lower()).first()
        if user is None:
            print(f"No account with the email {email}", file=sys.stderr)
            return 1
        password = getpass("New password: ")
        if password != getpass("Repeat it: "):
            print("The passwords do not match", file=sys.stderr)
            return 1
        try:
            _check_new_password(password)
        except HTTPException as exc:
            print(exc.detail, file=sys.stderr)
            return 1
        user.password_hash = _hash_password(password)
        user.token_version += 1
        session_repo.forget_all(db, user.id)
        auth_audit.record(db, user, "auth.password_reset", extra={"via": "operator-cli"}, commit=False)
        db.commit()
    print(f"Password set for {email}; all their sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
