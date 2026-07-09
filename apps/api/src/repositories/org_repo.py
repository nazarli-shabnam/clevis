from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.db import Org


def get_by_login(db: Session, github_login: str) -> Org | None:
    return db.query(Org).filter(Org.github_login == github_login).first()


def get_or_create(db: Session, github_login: str, github_org_id: int | None = None) -> Org:
    org = get_by_login(db, github_login)
    if org is not None:
        if github_org_id is not None and org.github_org_id is None:
            org.github_org_id = github_org_id
            db.commit()
            db.refresh(org)
        return org
    org = Org(github_login=github_login, github_org_id=github_org_id)
    db.add(org)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent insert of the same github_login (unique constraint).
        db.rollback()
        org = get_by_login(db, github_login)
        if org is None:
            raise
        return org
    db.refresh(org)
    return org
