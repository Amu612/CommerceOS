"""HITL automation: proposed/executed actions + the approval queue."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.v1.deps import auth_dependency
from app.automation import decide
from app.core.security import decode_token
from app.database.session import get_db
from app.exceptions.base import AuthorizationException
from app.models.operations import Approval, AutomationAction
from app.models.security import User, UserRole

router = APIRouter(prefix="/api/v1/automation", tags=["Automation"])


def _action_dict(a: AutomationAction, approval: Optional[Approval] = None) -> dict:
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
        "approval": None
        if not approval
        else {
            "id": approval.id,
            "required_role": approval.required_role,
            "status": approval.status,
            "decided_by": approval.decided_by,
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            "reason": approval.reason,
        },
    }


@router.get("/actions")
def list_actions(
    status: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    _: Optional[User] = Depends(auth_dependency()),
    db: Session = Depends(get_db),
):
    q = db.query(AutomationAction)
    if status:
        q = q.filter(AutomationAction.status == status)
    if agent:
        q = q.filter(AutomationAction.agent == agent)
    rows = q.order_by(desc(AutomationAction.created_at)).limit(limit).all()
    appr = {a.action_id: a for a in db.query(Approval).filter(Approval.action_id.in_([r.id for r in rows])).all()}
    return {
        "items": [_action_dict(r, appr.get(r.id)) for r in rows],
        "counts": {
            "auto_executed": q.filter(AutomationAction.mode == "AUTO").count(),
            "pending_approval": db.query(Approval).filter(Approval.status == "PENDING").count(),
            "blocked": db.query(AutomationAction).filter(AutomationAction.status == "BLOCKED").count(),
        },
    }


@router.get("/approvals")
def list_approvals(
    status: str = Query("PENDING"),
    current: Optional[User] = Depends(auth_dependency()),
    db: Session = Depends(get_db),
):
    q = db.query(Approval).filter(Approval.status == status)
    # No session (auth not enforced in this environment) sees everything, same
    # as a super admin would — there's no per-role identity to filter by.
    if current is not None and current.role != UserRole.SUPER_ADMIN:
        q = q.filter(Approval.required_role == current.role.value)
    approvals = q.order_by(desc(Approval.requested_at)).limit(200).all()
    actions = {
        a.id: a
        for a in db.query(AutomationAction).filter(AutomationAction.id.in_([x.action_id for x in approvals])).all()
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


def _actor(current: Optional[User]) -> dict:
    if current is None:
        # Auth isn't enforced in this environment — attribute the decision to
        # a generic console actor rather than blocking the demo dashboard.
        return {"sub": None, "username": "console", "role": UserRole.SUPER_ADMIN.value}
    return {"sub": current.id, "username": current.username, "role": current.role.value}


def _check_can_decide(db: Session, approval_id: str, current: Optional[User]) -> Approval:
    ap = db.query(Approval).filter(Approval.id == approval_id).first()
    if not ap:
        from app.exceptions.base import NotFoundException

        raise NotFoundException("Approval not found.")
    if current is not None and current.role != UserRole.SUPER_ADMIN and current.role.value != ap.required_role:
        raise AuthorizationException(f"This approval requires the {ap.required_role} role.")
    return ap


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecisionBody, current: Optional[User] = Depends(auth_dependency()), db: Session = Depends(get_db)):
    ap = _check_can_decide(db, approval_id, current)
    ap.reason = body.reason
    action = decide(db, approval_id, approved=True, actor=_actor(current))
    return _action_dict(action, ap)


@router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecisionBody, current: Optional[User] = Depends(auth_dependency()), db: Session = Depends(get_db)):
    ap = _check_can_decide(db, approval_id, current)
    ap.reason = body.reason
    action = decide(db, approval_id, approved=False, actor=_actor(current))
    return _action_dict(action, ap)
