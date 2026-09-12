"""
Centralized RBAC — the single source of truth for what each role can do.

Every authorization decision in this app — a FastAPI route dependency, a
per-record check inside a route body, the frontend's nav rendering — reads
from the functions here. Nothing outside this module (and the two mappings
in `app.models.security` it wraps) hardcodes a role comparison; that's what
keeps "who can do what" auditable in one place instead of scattered across
a dozen route files that drift out of sync with each other.

The model, in one paragraph: `SUPER_ADMIN` can do everything. Every other
role is a **domain admin** scoped to exactly one agent (`AGENT_ROLE_MAP` /
`ROLE_AGENTS` in `app.models.security` is the agent<->role pairing) — they
get 200 on their own agent's routes and 403 on every other agent's, on the
Orchestrator (a cross-domain view with no way to scope it to one agent), and
on any other domain's approvals or run history. A domain admin can still see
and decide the approvals that already require their own role — that's the
system's Human-In-The-Loop design working as intended, not a hole in it.
"""
from __future__ import annotations

from app.models.security import AGENT_ROLE_MAP, ROLE_AGENTS, UserRole

__all__ = [
    "is_super_admin",
    "agent_for_role",
    "role_for_agent",
    "can_access_agent",
    "can_access_orchestrator",
    "can_view_approval",
    "can_decide_approval",
    "can_access_run_history",
    "permitted_agents",
    "is_admin_only_route_allowed",
]


def is_super_admin(role: UserRole) -> bool:
    return role == UserRole.SUPER_ADMIN


def agent_for_role(role: UserRole) -> str | None:
    """The single agent a domain role owns, or None for SUPER_ADMIN / an
    unmapped role."""
    return ROLE_AGENTS.get(role)


def role_for_agent(agent: str) -> UserRole | None:
    """The role required to operate a given agent's routes."""
    return AGENT_ROLE_MAP.get(agent)


def can_access_agent(role: UserRole, agent: str) -> bool:
    """Can this role call `agent`'s analyze/query/monitor/etc. routes?"""
    if is_super_admin(role):
        return True
    return role_for_agent(agent) == role


def can_access_orchestrator(role: UserRole) -> bool:
    """The Orchestrator sweeps and correlates every domain agent at once —
    there is no way to scope it to a single agent, so only SUPER_ADMIN sees
    it. A domain admin's own agent view already shows everything they're
    entitled to."""
    return is_super_admin(role)


def can_view_approval(role: UserRole, required_role: str) -> bool:
    """Can this role see one specific approval (list it, read its detail)?"""
    if is_super_admin(role):
        return True
    return role.value == required_role


def can_decide_approval(role: UserRole, required_role: str) -> bool:
    """Can this role approve/reject one specific approval? Same rule as
    viewing it today, kept as a separate function because the two checks
    are conceptually different call sites and may legitimately diverge later
    (e.g. a future read-only auditor role)."""
    return can_view_approval(role, required_role)


def can_access_run_history(role: UserRole, agent: str | None) -> bool:
    """Can this role list/read agent-run history for `agent`? `agent=None`
    (an unscoped "all agents" query) is a cross-domain request and only
    SUPER_ADMIN may make it."""
    if is_super_admin(role):
        return True
    if agent is None:
        return False
    return role_for_agent(agent) == role


def permitted_agents(role: UserRole) -> list[str]:
    """Every agent key this role may call — all six for SUPER_ADMIN, exactly
    one for a domain admin. Used both for the `/auth/me` payload the frontend
    renders its nav from, and by tests asserting the boundary directly."""
    if is_super_admin(role):
        return sorted(AGENT_ROLE_MAP.keys())
    agent = agent_for_role(role)
    return [agent] if agent else []


def is_admin_only_route_allowed(role: UserRole) -> bool:
    """User management, audit log, and other SUPER_ADMIN-only administrative
    surfaces."""
    return is_super_admin(role)
