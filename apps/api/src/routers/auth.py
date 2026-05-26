"""
Auth router — /auth/*

Endpoints:
  POST /auth/setup          First-run only; creates the owner account (409 if users exist)
  POST /auth/login          Returns a JWT on valid credentials
  GET  /auth/me             Returns the current authenticated user
  PATCH /auth/me            Updates the current user's display name
  GET  /auth/setup-required Returns { setup_required: bool } for the UI routing decision
"""

from datetime import datetime
import logging

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.orm import Session

from src.core.auth import UserOut, create_access_token, require_auth
from src.core.db import User, get_db

logger = logging.getLogger(__name__)
router = APIRouter()

_MIN_PASSWORD_LEN = 12


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ── Schemas ───────────────────────────────────────────────────────────────────

class SetupRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None
    is_owner: bool
    created_at: datetime


class PatchMeRequest(BaseModel):
    name: str | None = None


class SetupRequiredResponse(BaseModel):
    setup_required: bool


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/setup-required", response_model=SetupRequiredResponse)
def setup_required(db: Session = Depends(get_db)):
    """No auth required — used by the UI before any redirect decision."""
    count = db.query(User).count()
    return {"setup_required": count == 0}


@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def setup(body: SetupRequest, db: Session = Depends(get_db)):
    """First-run only. Returns 409 if any user already exists."""
    if db.query(User).count() > 0:
        raise HTTPException(status_code=409, detail="Setup already complete")
    if len(body.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )
    user = User(
        email=body.email,
        name=body.name,
        password_hash=_hash_password(body.password),
        is_owner=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.email, user.is_owner)
    return {"access_token": token, "user": UserOut(id=user.id, email=user.email, name=user.name, is_owner=user.is_owner)}


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Returns a JWT on valid credentials. Always returns 401 on failure (no field leaking)."""
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user.id, user.email, user.is_owner)
    return {"access_token": token, "user": UserOut(id=user.id, email=user.email, name=user.name, is_owner=user.is_owner)}


@router.get("/me", response_model=MeResponse)
def get_me(current_user: UserOut = Depends(require_auth), db: Session = Depends(get_db)):
    """Returns full profile of the authenticated user."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/me", response_model=MeResponse)
def patch_me(
    body: PatchMeRequest,
    current_user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Update the authenticated user's display name."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.name is not None:
        user.name = body.name
    db.commit()
    db.refresh(user)
    return user
