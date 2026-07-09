from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.core.rbac import OrgContext, require_org_role
from src.schemas.cache import CacheClearInput, CacheClearResponse, CacheListInput, CacheListResponse
from src.services.cache_service import clear
from src.services.github_client import GitHubClient

router = APIRouter()


def _list_caches(owner: str, repo: str, payload: CacheListInput) -> CacheListResponse:
    client = GitHubClient(payload.token.get_secret_value())
    data = client.request("GET", f"/repos/{owner}/{repo}/actions/caches")
    return {"repository": f"{owner}/{repo}", "total": data.get("total_count", 0), "actions_caches": data.get("actions_caches", [])}


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/actions-caches", response_model=CacheListResponse)
def org_list_caches(
    org_login: str,
    owner: str,
    repo: str,
    payload: CacheListInput,
    ctx: OrgContext = Depends(require_org_role(min_role="member")),
):
    if owner != ctx.org.github_login:
        raise HTTPException(status_code=403, detail="owner must match the org in the URL")
    return _list_caches(owner, repo, payload)


@router.post("/orgs/{org_login}/repos/{owner}/{repo}/actions-caches/clear", response_model=CacheClearResponse)
def org_clear_caches(
    org_login: str,
    owner: str,
    repo: str,
    payload: CacheClearInput,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    if owner != ctx.org.github_login:
        raise HTTPException(status_code=403, detail="owner must match the org in the URL")
    return clear(db, owner, repo, payload)


@router.post("/me/repos/{owner}/{repo}/actions-caches", response_model=CacheListResponse)
def personal_list_caches(
    owner: str,
    repo: str,
    payload: CacheListInput,
    _user: UserOut = Depends(require_auth),
):
    return _list_caches(owner, repo, payload)


@router.post("/me/repos/{owner}/{repo}/actions-caches/clear", response_model=CacheClearResponse)
def personal_clear_caches(
    owner: str,
    repo: str,
    payload: CacheClearInput,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
):
    return clear(db, owner, repo, payload)
