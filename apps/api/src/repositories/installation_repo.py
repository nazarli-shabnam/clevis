from sqlalchemy.orm import Session

from src.models.installation import GitHubInstallation


def create(
    db: Session,
    account_login: str,
    account_type: str,
    auth_mode: str,
    installation_id: int | None = None,
) -> GitHubInstallation:
    token_ref = f"tok_{account_login}"
    row = GitHubInstallation(
        account_login=account_login,
        account_type=account_type,
        installation_id=installation_id,
        auth_mode=auth_mode,
        token_ref=token_ref,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
