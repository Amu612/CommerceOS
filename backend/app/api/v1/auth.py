"""Auth routes: login, refresh, current user."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.database.session import get_db
from app.exceptions.base import AuthenticationException
from app.api.v1.deps import get_current_user
from app.models.security import User
from app.services.auth_service import get_user_by_id, authenticate_user, log_audit

router = APIRouter(prefix="/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


def _user_public(user: User) -> dict:
    """
    The single payload the frontend renders its nav and route guards from —
    `permitted_agents` / `can_access_orchestrator` come straight from
    `app.core.rbac`, the same module every backend route dependency reads,
    so the UI can never drift out of sync with what the server will actually
    allow (it also means a client bug can only ever hide too much, never
    grant access the API itself would refuse).
    """
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
        "permitted_agents": rbac.permitted_agents(user.role),
        "can_access_orchestrator": rbac.can_access_orchestrator(user.role),
        "is_super_admin": rbac.is_super_admin(user.role),
    }


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else None
    rid = request.headers.get("x-request-id")
    user = authenticate_user(db, body.username, body.password)
    if not user:
        log_audit(db, action="LOGIN_FAILURE", actor_username=body.username, ip_address=ip, request_id=rid, status_code=401)
        raise AuthenticationException("Invalid username or password.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    log_audit(
        db,
        action="LOGIN_SUCCESS",
        actor_id=user.id,
        actor_username=user.username,
        actor_role=user.role.value,
        ip_address=ip,
        request_id=rid,
        status_code=200,
    )
    return TokenResponse(
        access_token=create_access_token(user.id, role=user.role.value, username=user.username),
        refresh_token=create_refresh_token(user.id),
        user=_user_public(user),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise AuthenticationException("Invalid refresh token.")
    user = get_user_by_id(db, payload.get("sub", ""))
    if not user or not user.is_active:
        raise AuthenticationException("Unknown or inactive user.")
    return TokenResponse(
        access_token=create_access_token(user.id, role=user.role.value, username=user.username),
        refresh_token=create_refresh_token(user.id),
        user=_user_public(user),
    )


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return _user_public(current_user)


@router.post("/logout")
def logout(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Stateless JWT: client discards tokens. We record the event.
    log_audit(
        db,
        action="LOGOUT",
        actor_id=current_user.id,
        actor_username=current_user.username,
        actor_role=current_user.role.value,
        ip_address=request.client.host if request.client else None,
        request_id=request.headers.get("x-request-id"),
        status_code=200,
    )
    return {"status": "ok"}
