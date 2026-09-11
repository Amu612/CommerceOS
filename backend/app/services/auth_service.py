"""
Authentication service: user lookup, credential verification, audit logging.
Token creation/decoding lives in `app.core.security`.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.models.security import AuditLog, User, UserRole, _uuid

logger = get_logger("auth")


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_user(db: Session, *, username: str, email: str, password: str, role: UserRole) -> User:
    user = User(
        id=_uuid(),
        username=username,
        email=email,
        hashed_password=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def log_audit(
    db: Session,
    *,
    action: str,
    actor_id: Optional[str] = None,
    actor_username: Optional[str] = None,
    actor_role: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    status_code: Optional[int] = None,
    latency_ms: Optional[float] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    entry = AuditLog(
        id=_uuid(),
        action=action,
        actor_id=actor_id,
        actor_username=actor_username,
        actor_role=actor_role,
        target_type=target_type,
        target_id=target_id,
        status_code=status_code,
        latency_ms=latency_ms,
        details=details,
        ip_address=ip_address,
        request_id=request_id,
    )
    db.add(entry)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - audit must never break the request
        db.rollback()
        logger.warning("audit_persist_failed", action=action)
