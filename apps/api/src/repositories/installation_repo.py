from sqlalchemy.orm import Session

from src.core.db import GitHubInstallation


def create(
    db: Session,
    account_login: str,
    account_type: str,
    auth_mode: str,
    installation_id: int | None = None,
    org_id: int | None = None,
    owner_user_id: int | None = None,
) -> GitHubInstallation:
    token_ref = f"tok_{account_login}"
    row = GitHubInstallation(
        account_login=account_login,
        account_type=account_type,
        installation_id=installation_id,
        auth_mode=auth_mode,
        token_ref=token_ref,
        org_id=org_id,
        owner_user_id=owner_user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_for_org(db: Session, org_id: int) -> list[GitHubInstallation]:
    return (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.org_id == org_id)
        .order_by(GitHubInstallation.created_at.desc())
        .all()
    )


def list_for_user(db: Session, owner_user_id: int) -> list[GitHubInstallation]:
    return (
        db.query(GitHubInstallation)
        .filter(GitHubInstallation.owner_user_id == owner_user_id)
        .order_by(GitHubInstallation.created_at.desc())
        .all()
    )
