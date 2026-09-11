"""
Password hashing (bcrypt) and JWT (HS256 via python-jose).

Kept dependency-light and side-effect free so it is trivially unit-testable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from jose import JWTError, jwt

from app.core.settings import settings

_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, *, role: str, username: str, expires_minutes: Optional[int] = None) -> str:
    ttl = expires_minutes if expires_minutes is not None else settings.JWT_TTL_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "username": username,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_REFRESH_TTL_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None
