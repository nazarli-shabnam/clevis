from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.db import get_db
from src.schemas.cache import CacheClearInput, CacheClearResponse, CacheListInput, CacheListResponse
from src.services.cache_service import clear
from src.services.github_client import GitHubClient

router = APIRouter()


@router.post("/{owner}/{repo}/actions-caches", response_model=CacheListResponse)
def list_caches(owner: str, repo: str, payload: CacheListInput, _user: UserOut = Depends(require_auth)):
    client = GitHubClient(payload.token.get_secret_value())
    data = client.request("GET", f"/repos/{owner}/{repo}/actions/caches")
    return {"repository": f"{owner}/{repo}", "total": data.get("total_count", 0), "actions_caches": data.get("actions_caches", [])}


@router.post("/{owner}/{repo}/actions-caches/clear", response_model=CacheClearResponse)
def clear_caches(
    owner: str,
    repo: str,
    payload: CacheClearInput,
    db: Session = Depends(get_db),
    _user: UserOut = Depends(require_auth),
):
    return clear(db, owner, repo, payload)
