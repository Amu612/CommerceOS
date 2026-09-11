"""
Auth dependencies. Unknown/invalid/expired tokens ⇒ 401. No user is ever
fabricated from an unverified token subject.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database.session import get_db
from app.exceptions.base import AuthenticationException, AuthorizationException
from app.models.security import AGENT_ROLE_MAP, User, UserRole
from app.services.auth_service import get_user_by_id


def _extract_token(authorization: Optional[str], token: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return token


def get_current_user(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None, alias="token"),
    db: Session = Depends(get_db),
) -> User:
    raw = _extract_token(authorization, token)
    if not raw:
        raise AuthenticationException("Authentication required.")

    payload = decode_token(raw)
    if not payload or payload.get("type") != "access":
        raise AuthenticationException("Invalid or expired token.")

    user = get_user_by_id(db, payload.get("sub", ""))
    if not user:
        raise AuthenticationException("Unknown user.")
    if not user.is_active:
        raise AuthenticationException("Account is inactive.")
    return user


def get_optional_user(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None, alias="token"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    try:
        return get_current_user(authorization=authorization, token=token, db=db)
    except AuthenticationException:
        return None


def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.SUPER_ADMIN:
        raise AuthorizationException("Super admin access required.")
    return current_user


def require_roles(*roles: UserRole):
    allowed = set(roles) | {UserRole.SUPER_ADMIN}

    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise AuthorizationException("Insufficient role for this operation.")
        return current_user

    return _check


def auth_dependency():
    """
    Returns the auth dependency to attach to read/query agent routes.
    Enforced when `settings.AUTH_ENFORCED` (always in production); a soft
    optional-user check otherwise so local dev / the demo dashboard works
    without a login wall. Mutating/admin routes always use the strict deps.
    """
    from app.core.settings import settings

    return get_current_user if settings.AUTH_ENFORCED else get_optional_user


def require_agent_access(agent: str):
    """Write access to a domain agent's controls requires the matching admin role."""

    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role == UserRole.SUPER_ADMIN:
            return current_user
        required = AGENT_ROLE_MAP.get(agent)
        if required is None or current_user.role != required:
            raise AuthorizationException(f"Access denied for agent '{agent}'.")
        return current_user

    return _check
