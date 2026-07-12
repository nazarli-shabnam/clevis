"""GET /me/orgs — the current user's org memberships, for the UI's org/personal context switcher."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import Org, get_db
from src.repositories import org_membership_repo
from src.schemas.org import MyOrgMembershipOut

router = APIRouter()


@router.get("/me/orgs", response_model=list[MyOrgMembershipOut])
def list_my_orgs(user: UserOut = Depends(require_auth), db: Session = Depends(get_db)):
    memberships = org_membership_repo.list_for_user(db, user_id=user.id)
    org_by_id = {org.id: org for org in db.query(Org).filter(Org.id.in_([m.org_id for m in memberships])).all()}
    return [
        {"org_login": org_by_id[m.org_id].github_login, "role": m.role}
        for m in memberships
        if m.org_id in org_by_id
    ]
