from sqlalchemy.orm import Session

from src.core.db import GitHubInstallation


def upsert(
    db: Session,
    account_login: str,
    account_type: str,
    auth_mode: str,
    installation_id: int | None = None,
    org_id: int | None = None,
    owner_user_id: int | None = None,
) -> GitHubInstallation:
    if (org_id is None) == (owner_user_id is None):
        raise ValueError("Exactly one of org_id or owner_user_id must be set")

    query = db.query(GitHubInstallation).filter(GitHubInstallation.account_login == account_login)
    if org_id is not None:
        query = query.filter(GitHubInstallation.org_id == org_id)
    else:
        query = query.filter(GitHubInstallation.owner_user_id == owner_user_id)

    existing = query.first()
    token_ref = f"tok_{account_login}"
    if existing:
        existing.account_type = account_type
        existing.auth_mode = auth_mode
        existing.installation_id = installation_id
        existing.token_ref = token_ref
        db.commit()
        db.refresh(existing)
        return existing

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


def create(
    db: Session,
    account_login: str,
    account_type: str,
    auth_mode: str,
    installation_id: int | None = None,
    org_id: int | None = None,
    owner_user_id: int | None = None,
) -> GitHubInstallation:
    return upsert(
        db,
        account_login=account_login,
        account_type=account_type,
        auth_mode=auth_mode,
        installation_id=installation_id,
        org_id=org_id,
        owner_user_id=owner_user_id,
    )


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


def delete_by_installation_id(db: Session, installation_id: int) -> None:
    db.query(GitHubInstallation).filter(GitHubInstallation.installation_id == installation_id).delete()
    db.commit()
