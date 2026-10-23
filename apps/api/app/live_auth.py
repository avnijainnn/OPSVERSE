"""Live-only accounts and workspace access. Demo routes remain unauthenticated and synthetic."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterator

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .live_models import AuditRecord, Membership, User, Workspace, session_factory
from .live_rate_limit import check_auth_budget

router = APIRouter(prefix="/live")
password_hash = PasswordHash.recommended()
COOKIE = "opsverse_session"


def secret() -> str:
    value = os.getenv("SESSION_SECRET", "")
    if len(value) < 32:
        raise HTTPException(503, "Live Operations requires a 32+ character SESSION_SECRET")
    return value


def get_db() -> Iterator[Session]:
    try:
        factory = session_factory()
        with factory() as db:
            yield db
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def same_origin(request: Request) -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        allowed = {item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")}
        if request.cookies.get(COOKIE) and not origin:
            raise HTTPException(403, "Origin header is required for an authenticated write")
        if origin and origin not in allowed:
            raise HTTPException(403, "Origin is not allowed")


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normal_email(cls, value: str) -> str:
        value = value.strip().lower()
        if value.count("@") != 1 or "." not in value.split("@", 1)[1]:
            raise ValueError("Enter a valid email address")
        return value


class Registration(Credentials):
    workspace_name: str = Field(min_length=2, max_length=160)


def issue_session(response: Response, user_id: str) -> None:
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": user_id, "iss": "opsverse", "iat": now,
                        "exp": now + timedelta(hours=12)}, secret(), algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=os.getenv("COOKIE_SECURE") == "true",
                        samesite="lax", max_age=12 * 3600, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sign in to Live Operations")
    try:
        claims = jwt.decode(token, secret(), algorithms=["HS256"], issuer="opsverse",
                            options={"require": ["sub", "exp", "iss"]})
    except InvalidTokenError as exc:
        raise HTTPException(401, "Session expired or invalid") from exc
    user = db.get(User, claims["sub"])
    if user is None:
        raise HTTPException(401, "Account no longer exists")
    return user


def workspace_member(workspace_id: str, user: User, db: Session) -> Membership:
    member = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id,
                                                 Membership.user_id == user.id))
    if member is None:
        # Conceal existence of other businesses.
        raise HTTPException(404, "Workspace not found")
    return member


def require_workspace_role(workspace_id: str, user: User, db: Session,
                           allowed: tuple[str, ...] = ("owner", "analyst")) -> Membership:
    member = workspace_member(workspace_id, user, db)
    if member.role not in allowed:
        raise HTTPException(403, "Your workspace role cannot perform this action")
    return member


def account_data(db: Session, user: User) -> dict:
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    workspaces = [db.get(Workspace, membership.workspace_id) for membership in memberships]
    return {"id": user.id, "email": user.email,
            "workspaces": [{"id": item.id, "name": item.name} for item in workspaces if item]}


@router.post("/register", dependencies=[Depends(same_origin)])
def register(data: Registration, response: Response, request: Request,
             db: Session = Depends(get_db)):
    secret()
    if os.getenv("REGISTRATION_ENABLED", "true").strip().lower() != "true":
        raise HTTPException(403, "Pilot registration is closed; contact the workspace owner")
    check_auth_budget(data.email, request.client.host if request.client else "unknown", "register")
    if db.scalar(select(User.id).where(User.email == data.email)):
        raise HTTPException(409, "Account already exists")
    user = User(email=data.email, password_hash=password_hash.hash(data.password))
    db.add(user)
    db.flush()
    workspace = Workspace(name=data.workspace_name.strip(), owner_id=user.id)
    db.add(workspace)
    db.flush()
    db.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.add(AuditRecord(workspace_id=workspace.id, user_id=user.id, action="workspace_created",
                       target_id=workspace.id, detail={}))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Account already exists") from exc
    issue_session(response, user.id)
    return account_data(db, user)


@router.post("/login", dependencies=[Depends(same_origin)])
def login(data: Credentials, response: Response, request: Request,
          db: Session = Depends(get_db)):
    secret()
    check_auth_budget(data.email, request.client.host if request.client else "unknown", "login")
    user = db.scalar(select(User).where(User.email == data.email))
    if user is None or not password_hash.verify(data.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    issue_session(response, user.id)
    return account_data(db, user)


@router.post("/logout", dependencies=[Depends(same_origin)])
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return account_data(db, user)
