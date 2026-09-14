"""
Automation executor + proposal service.

  propose_for_run(agent, findings)  → creates AutomationAction rows, auto-executes
                                      the AUTO ones (+ verify), queues the rest.
  decide(approval_id, approved, user) → executes an approved action, or rejects it.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.automation.actions import FINDING_ACTION_MAP, HANDLERS
from app.automation.langchain_executor import execute_via_langchain
from app.automation.policies import Mode, evaluate
from app.core.logging import get_logger
from app.models.operations import Approval, AutomationAction
from app.models.security import _uuid
from app.services.auth_service import log_audit
from app.services.event_bus import publish_event

logger = get_logger("automation.executor")

# A handler opens its own DB session (see `automation.actions._notify`) that's
# independent of the caller's — under SQLite that's a second writer against
# the same file. WAL mode + busy_timeout (see `database.session`) make that
# wait instead of erroring, but a couple of retries here is cheap insurance
# against the rare case a transient lock outlives the timeout.
_MAX_EXECUTE_ATTEMPTS = 3

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
            # Commit the proposal itself before attempting execution: `_execute`
            # shares this session with its handler now (see actions.py) and
            # rolls it back on a handler failure — committing first means that
            # rollback can only ever undo the *handler's* effect, never the
            # just-flushed AutomationAction row itself.
            db.commit()
            # use_langchain=False here on purpose: a single agent run (or an
            # orchestrator sweep of all six) can propose a dozen+ AUTO actions
            # in one request, each waiting for the SQLite write lock behind
            # the last — adding a real LLM round-trip per action turned one
            # sweep into 30-70+ seconds. The LangChain-driven, narrated
            # execution path is reserved for `decide()` below, where a human
            # just approved exactly one action and is already waiting on it.
            _execute(db, action, use_langchain=False)
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


def _execute(db: Session, action: AutomationAction, *, use_langchain: bool = True) -> None:
    handler = HANDLERS.get(action.action_type)
    if not handler:
        action.status = "FAILED"
        action.result = {"error": f"no handler for {action.action_type}"}
        return
    run_fn, verify_fn, _ = handler
    payload = action.payload or {}

    for attempt in range(1, _MAX_EXECUTE_ATTEMPTS + 1):
        try:
            if use_langchain:
                result = execute_via_langchain(action.action_type, payload, db, run_fn)
            else:
                result = run_fn(payload, db)
                result["automation_backend"] = "deterministic"
            action.result = result
            action.status = "EXECUTED"
            action.executed_at = datetime.now(timezone.utc)
            ok = bool(verify_fn(payload, result))
            action.verification = {"verified": ok, "at": datetime.now(timezone.utc).isoformat()}
            action.status = "VERIFIED" if ok else "EXECUTED"
            logger.info(
                "automation_executed", action=action.action_type, verified=ok,
                backend=result.get("automation_backend"), attempt=attempt,
            )
            # Real-time proof push: the console's approval queue refreshes the
            # moment an action actually executes (auto or human-approved).
            publish_event("automation", {
                "type": "automation_executed",
                "action_type": action.action_type,
                "status": action.status,
                "verified": ok,
                "backend": result.get("automation_backend"),
            })
            return
        except OperationalError as exc:
            db.rollback()  # clear the failed flush so `db` is usable again
            transient = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            if transient and attempt < _MAX_EXECUTE_ATTEMPTS:
                logger.warning(
                    "automation_execute_retry", action=action.action_type, attempt=attempt, error=str(exc),
                )
                time.sleep(0.3 * attempt)
                continue
            action.status = "FAILED"
            action.result = {"error": str(exc)}
            logger.warning("automation_execute_failed", action=action.action_type, error=str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            action.status = "FAILED"
            action.result = {"error": str(exc)}
            logger.warning("automation_execute_failed", action=action.action_type, error=str(exc))
            return


def decide(db: Session, approval_id: str, *, approved: bool, actor: dict) -> AutomationAction:
    approval = db.query(Approval).filter(Approval.id == approval_id).first()
    if not approval:
        from app.exceptions.base import NotFoundException

        raise NotFoundException("Approval not found.")
    action = db.query(AutomationAction).filter(AutomationAction.id == approval.action_id).first()

    approval.status = "APPROVED" if approved else "REJECTED"
    approval.decided_by = actor.get("username")
    approval.decided_at = datetime.now(timezone.utc)
    # Commit the human decision itself before attempting execution — `_execute`
    # shares this session with its handler and rolls it back on failure, which
    # must never also erase the fact that a human already approved/rejected.
    db.commit()

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
