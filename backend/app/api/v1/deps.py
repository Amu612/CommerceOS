"""
Auth dependencies. Unknown/invalid/expired tokens ⇒ 401. No user is ever
fabricated from an unverified token subject.
"""

from __future__ import annotations

from fastapi import Depends, Header, Query
from sqlalchemy.orm import Session

from app.core import rbac
from app.core.security import decode_token
from app.database.session import get_db
from app.exceptions.base import AuthenticationException, AuthorizationException
from app.models.security import User, UserRole
from app.services.auth_service import get_user_by_id


def _extract_token(authorization: str | None, token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return token


def get_current_user(
    authorization: str | None = Header(None),
    token: str | None = Query(None, alias="token"),
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
    authorization: str | None = Header(None),
    token: str | None = Query(None, alias="token"),
    db: Session = Depends(get_db),
) -> User | None:
    try:
        return get_current_user(authorization=authorization, token=token, db=db)
    except AuthenticationException:
        return None


def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if not rbac.is_admin_only_route_allowed(current_user.role):
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
    """Write access to a domain agent's controls requires the matching admin
    role. Always strict (unlike `agent_dependency` below) — for routes that
    mutate state and must never run un-authenticated regardless of
    AUTH_ENFORCED."""

    def _check(current_user: User = Depends(get_current_user)) -> User:
        if not rbac.can_access_agent(current_user.role, agent):
            raise AuthorizationException(f"Access denied for agent '{agent}'.")
        return current_user

    return _check


def agent_dependency(agent: str):
    """
    RBAC gate for one domain agent's entire router (mounted per-agent in
    main.py) — an ORDERS_ADMIN gets 403 from every Inventory route, an
    INVENTORY_ADMIN gets 403 from every Orders route, and so on. SUPER_ADMIN
    always passes. See `app.core.rbac` for the single source of truth this
    reads from.

    Mirrors `auth_dependency()`'s soft/strict split so local dev without
    AUTH_ENFORCED keeps working un-authenticated: no session at all is let
    through (there's no role to check), but a *present* session whose role
    doesn't match this agent is still denied — RBAC isn't only a
    production-mode feature.
    """

    def _check(current_user: User | None = Depends(auth_dependency())) -> User | None:
        if current_user is None:
            return None
        if not rbac.can_access_agent(current_user.role, agent):
            raise AuthorizationException(
                f"Your role ({current_user.role.value}) doesn't have access to the '{agent}' agent."
            )
        return current_user

    return _check


def ingestion_dependency():
    """
    RBAC gate for the data-ingestion/replay-control endpoints
    (`/api/orders/ingestion/*`, `/api/simulation/*`, `/dashboard/simulation/*`
    — three URL aliases over the exact same handler). This mutates the one
    shared dataset every agent reads, and `reset` can wipe it outright, so
    it's SUPER_ADMIN-only — the same rule applied consistently across all
    three aliases, which is the point: before this, only the `/api/orders/...`
    path happened to inherit Orders' agent-scoped gate, while the other two
    aliases sat on the generic "any authenticated user" check and let any
    domain admin reset the whole platform's data through them.
    """

    def _check(current_user: User | None = Depends(auth_dependency())) -> User | None:
        if current_user is None:
            return None
        if not rbac.is_super_admin(current_user.role):
            raise AuthorizationException(
                f"Your role ({current_user.role.value}) doesn't have access to ingestion controls — "
                "restricted to SUPER_ADMIN."
            )
        return current_user

    return _check


def orchestrator_dependency():
    """
    RBAC gate for the Orchestrator router — a cross-domain sweep of every
    agent at once, so only SUPER_ADMIN may reach it (see
    `rbac.can_access_orchestrator`). Same soft/strict split as
    `agent_dependency`: no session at all passes through when AUTH_ENFORCED
    is off, but a present, non-super-admin session is always denied.
    """

    def _check(current_user: User | None = Depends(auth_dependency())) -> User | None:
        if current_user is None:
            return None
        if not rbac.can_access_orchestrator(current_user.role):
            raise AuthorizationException(
                f"Your role ({current_user.role.value}) doesn't have access to the Orchestrator — "
                "it sweeps every domain agent at once and is restricted to SUPER_ADMIN."
            )
        return current_user

    return _check
