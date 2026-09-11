"""
Automation executor + proposal service.

  propose_for_run(agent, findings)  → creates AutomationAction rows, auto-executes
                                      the AUTO ones (+ verify), queues the rest.
  decide(approval_id, approved, user) → executes an approved action, or rejects it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.automation.actions import FINDING_ACTION_MAP, HANDLERS
from app.automation.policies import Mode, evaluate
from app.core.logging import get_logger
from app.models.operations import Approval, AutomationAction
from app.models.security import _uuid
from app.services.auth_service import log_audit
from app.services.event_bus import publish_event

logger = get_logger("automation.executor")

_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def propose_for_run(
    db: Session,
    *,
    agent: str,
    findings: list[dict[str, Any]],
    decision_id: Optional[str] = None,
) -> list[AutomationAction]:
    """One proposed action per finding that maps to an action type."""
    created: list[AutomationAction] = []
    for f in findings:
        cat = f.get("category", "")
        action_type = FINDING_ACTION_MAP.get(cat)
        if not action_type:
            continue
        confidence = float(f.get("confidence") or 0.0)
        severity = f.get("severity", "MEDIUM")

        # de-dupe: skip if an open action of this (agent, type, title) already exists
        existing = (
            db.query(AutomationAction)
            .filter(
                AutomationAction.agent == agent,
                AutomationAction.action_type == action_type,
                AutomationAction.title == (f.get("title") or action_type)[:300],
                AutomationAction.status.in_(["PROPOSED", "EXECUTED", "VERIFIED"]),
            )
            .first()
        )
        if existing:
            continue

        decision = evaluate(agent, action_type, confidence=confidence, severity=severity)
        action = AutomationAction(
            id=_uuid(),
            agent=agent,
            action_type=action_type,
            title=(f.get("title") or action_type)[:300],
            detail=f.get("recommended_action") or f.get("what_happened"),
            decision_id=decision_id,
            mode=decision.mode.value,
            status="BLOCKED" if decision.mode == Mode.BLOCKED else "PROPOSED",
            confidence=confidence,
            payload={
                "agent": agent, "severity": severity, "category": cat,
                "title": f.get("title"), "detail": f.get("recommended_action"),
                "evidence": f.get("evidence"),
            },
        )
        db.add(action)
        db.flush()

        if decision.mode == Mode.AUTO:
            _execute(db, action)
        elif decision.mode == Mode.NEEDS_APPROVAL:
            db.add(Approval(
                id=_uuid(), action_id=action.id, required_role=decision.required_role, status="PENDING",
            ))
        created.append(action)

    db.commit()
    if created:
        publish_event("automation", {
            "type": "actions_proposed", "agent": agent,
            "count": len(created),
            "auto": sum(1 for a in created if a.mode == "AUTO"),
            "pending": sum(1 for a in created if a.mode == "NEEDS_APPROVAL"),
        })
    return created


def _execute(db: Session, action: AutomationAction) -> None:
    handler = HANDLERS.get(action.action_type)
    if not handler:
        action.status = "FAILED"
        action.result = {"error": f"no handler for {action.action_type}"}
        return
    run_fn, verify_fn, _ = handler
    try:
        result = run_fn(action.payload or {})
        action.result = result
        action.status = "EXECUTED"
        action.executed_at = datetime.now(timezone.utc)
        ok = bool(verify_fn(action.payload or {}, result))
        action.verification = {"verified": ok, "at": datetime.now(timezone.utc).isoformat()}
        action.status = "VERIFIED" if ok else "EXECUTED"
        logger.info("automation_executed", action=action.action_type, verified=ok)
    except Exception as exc:  # noqa: BLE001
        action.status = "FAILED"
        action.result = {"error": str(exc)}
        logger.warning("automation_execute_failed", action=action.action_type, error=str(exc))


def decide(db: Session, approval_id: str, *, approved: bool, actor: dict) -> AutomationAction:
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        from app.exceptions.base import NotFoundException

        raise NotFoundException("Approval not found.")
    action = db.query(AutomationAction).filter(AutomationAction.id == approval.action_id).first()

    approval.status = "APPROVED" if approved else "REJECTED"
    approval.decided_by = actor.get("username")
    approval.decided_at = datetime.now(timezone.utc)

    if approved:
        if action.action_type in HANDLERS:
            _execute(db, action)
        else:
            # No executable handler (e.g. CREATE_PURCHASE_ORDER_REQUEST) — mark actioned.
            action.status = "EXECUTED"
            action.executed_at = datetime.now(timezone.utc)
            action.result = {"note": "Approved — handed to the domain team (no in-system handler)."}
    else:
        action.status = "REJECTED"

    log_audit(
        db, action=f"AUTOMATION_{'APPROVE' if approved else 'REJECT'}",
        actor_id=actor.get("sub"), actor_username=actor.get("username"), actor_role=actor.get("role"),
        target_type="automation_action", target_id=action.id,
        details={"action_type": action.action_type},
    )
    db.commit()
    publish_event("automation", {"type": "approval_decided", "approved": approved, "action_type": action.action_type})
    return action
