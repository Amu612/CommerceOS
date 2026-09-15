"""HITL automation: proposed/executed actions + the approval queue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.v1.deps import auth_dependency
from app.automation import decide
from app.core import rbac
from app.database.session import get_db
from app.exceptions.base import AuthorizationException, NotFoundException
from app.models.operations import Approval, AutomationAction
from app.models.security import User, UserRole

router = APIRouter(prefix="/api/v1/automation", tags=["Automation"])


def _action_dict(a: AutomationAction, approval: Approval | None = None) -> dict:
    return {
        "id": a.id,
        "agent": a.agent,
        "action_type": a.action_type,
        "title": a.title,
        "detail": a.detail,
        "mode": a.mode,
        "status": a.status,
        "confidence": a.confidence,
        "payload": a.payload,
        "result": a.result,
        "verification": a.verification,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "executed_at": a.executed_at.isoformat() if a.executed_at else None,
        "approval": (
            None
            if not approval
            else {
                "id": approval.id,
                "required_role": approval.required_role,
                "status": approval.status,
                "decided_by": approval.decided_by,
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "reason": approval.reason,
            }
        ),
    }


@router.get("/actions")
def list_actions(
    status: str | None = Query(None),
    agent: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current: User | None = Depends(auth_dependency()),
    db: Session = Depends(get_db),
):
    # Same self-scoping rule as `runs.list_runs`: a domain admin may only see
    # their own agent's automation history. An explicit `agent=<other>` is a
    # cross-domain request (403); no `agent` at all is narrowed to their one
    # agent rather than 403ing, so their own agent view keeps working
    # unchanged. `current is None` only happens with AUTH_ENFORCED off (no
    # session at all, not a role to scope by) — unrestricted, matching every
    # other soft-mode dev-convenience route in this app.
    if current is not None and not rbac.is_super_admin(current.role):
        if agent is not None and not rbac.can_access_agent(current.role, agent):
            raise AuthorizationException(f"Access denied for agent '{agent}' automation history.")
        agent = agent or rbac.agent_for_role(current.role)

    q = db.query(AutomationAction)
    if status:
        q = q.filter(AutomationAction.status == status)
    if agent:
        q = q.filter(AutomationAction.agent == agent)
    rows = q.order_by(desc(AutomationAction.created_at)).limit(limit).all()
    appr = {
        a.action_id: a for a in db.query(Approval).filter(Approval.action_id.in_([r.id for r in rows])).all()
    }

    # Counts must respect the same agent scope as the list above, or a domain
    # admin's KPI tiles would silently leak system-wide totals even though
    # the rows they can see are correctly narrowed.
    counts_base = db.query(AutomationAction)
    approvals_base = db.query(Approval)
    if agent:
        counts_base = counts_base.filter(AutomationAction.agent == agent)
        approvals_base = approvals_base.join(
            AutomationAction, AutomationAction.id == Approval.action_id
        ).filter(AutomationAction.agent == agent)
    return {
        "items": [_action_dict(r, appr.get(r.id)) for r in rows],
        "counts": {
            "auto_executed": counts_base.filter(AutomationAction.mode == "AUTO").count(),
            "pending_approval": approvals_base.filter(Approval.status == "PENDING").count(),
            "blocked": counts_base.filter(AutomationAction.status == "BLOCKED").count(),
        },
    }


@router.get("/approvals")
def list_approvals(
    status: str = Query("PENDING"),
    current: User | None = Depends(auth_dependency()),
    db: Session = Depends(get_db),
):
    q = db.query(Approval).filter(Approval.status == status)
    # No session (auth not enforced in this environment) sees everything, same
    # as a super admin would — there's no per-role identity to filter by.
    if current is not None and not rbac.is_super_admin(current.role):
        q = q.filter(Approval.required_role == current.role.value)
    approvals = q.order_by(desc(Approval.requested_at)).limit(200).all()
    actions = {
        a.id: a
        for a in db.query(AutomationAction)
        .filter(AutomationAction.id.in_([x.action_id for x in approvals]))
        .all()
    }
    return {
        "items": [
            {**_action_dict(actions[ap.action_id], ap), "can_decide": True}
            for ap in approvals
            if ap.action_id in actions
        ]
    }


class DecisionBody(BaseModel):
    reason: str | None = None


def _actor(current: User | None) -> dict:
    if current is None:
        # Auth isn't enforced in this environment — attribute the decision to
        # a generic console actor rather than blocking the demo dashboard.
        return {"sub": None, "username": "console", "role": UserRole.SUPER_ADMIN.value}
    return {"sub": current.id, "username": current.username, "role": current.role.value}


def _check_can_decide(db: Session, approval_id: str, current: User | None) -> Approval:
    ap = db.query(Approval).filter(Approval.id == approval_id).first()
    if not ap:
        raise NotFoundException("Approval not found.")
    if current is not None and not rbac.can_decide_approval(current.role, ap.required_role):
        raise AuthorizationException(f"This approval requires the {ap.required_role} role.")
    return ap


@router.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    body: DecisionBody,
    current: User | None = Depends(auth_dependency()),
    db: Session = Depends(get_db),
):
    ap = _check_can_decide(db, approval_id, current)
    ap.reason = body.reason
    action = decide(db, approval_id, approved=True, actor=_actor(current))
    return _action_dict(action, ap)


@router.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: str,
    body: DecisionBody,
    current: User | None = Depends(auth_dependency()),
    db: Session = Depends(get_db),
):
    ap = _check_can_decide(db, approval_id, current)
    ap.reason = body.reason
    action = decide(db, approval_id, approved=False, actor=_actor(current))
    return _action_dict(action, ap)
