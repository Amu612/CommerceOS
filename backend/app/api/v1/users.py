"""User administration (SUPER_ADMIN only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import require_super_admin
from app.database.session import get_db
from app.exceptions.base import ConflictException, NotFoundException
from app.models.security import User, UserRole
from app.services.auth_service import create_user, get_user_by_username, log_audit

router = APIRouter(prefix="/users", tags=["Users"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    role: UserRole


def _public(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "role": u.role.value,
        "is_active": u.is_active,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


@router.get("")
def list_users(admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return {"users": [_public(u) for u in db.query(User).order_by(User.username).all()]}


@router.post("", status_code=201)
def create(
    body: CreateUserRequest, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)
):
    if get_user_by_username(db, body.username):
        raise ConflictException("Username already exists.")
    user = create_user(db, username=body.username, email=body.email, password=body.password, role=body.role)
    log_audit(
        db,
        action="USER_CREATE",
        actor_id=admin.id,
        actor_username=admin.username,
        target_type="user",
        target_id=user.id,
        details={"role": body.role.value},
    )
    return _public(user)


@router.patch("/{user_id}/deactivate")
def deactivate(user_id: str, admin: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundException("User not found.")
    user.is_active = False
    db.commit()
    log_audit(
        db,
        action="USER_DEACTIVATE",
        actor_id=admin.id,
        actor_username=admin.username,
        target_type="user",
        target_id=user_id,
    )
    return _public(user)
