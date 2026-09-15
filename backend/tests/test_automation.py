"""
Automation executor + policy tests.

Covers the two real bugs found and fixed in this pass:
  1. A same-thread SQLite deadlock: a handler used to open a second
     `SessionLocal()` while the caller's session already held an uncommitted
     write, so it always ran out the busy-timeout and landed on FAILED. Every
     AUTO action here must reach VERIFIED, never FAILED, with zero retries.
  2. CRITICAL-severity findings must always require manual approval, even for
     an action type ("FLAG_FOR_REVIEW" etc.) that is otherwise low-blast-radius
     and auto-executes at lower severities.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.automation import decide, propose_for_run
from app.database.session import SessionLocal
from app.models.operations import Approval, AutomationAction


def _finding(**overrides) -> dict:
    base = {
        "category": "LATE_DELIVERY_RISK",  # -> FLAG_FOR_REVIEW, AUTO-eligible
        "severity": "MEDIUM",
        "confidence": 0.9,
        "title": f"test finding {uuid.uuid4().hex[:8]}",
        "recommended_action": "Do the thing.",
        "evidence": "carrier=TestCarrier",
    }
    base.update(overrides)
    return base


def test_auto_action_executes_and_verifies_without_a_second_session():
    """Regression test for the same-thread SQLite deadlock: an AUTO action's
    handler must run to completion (VERIFIED), not FAILED, using only the
    caller's session — no `database is locked` from a nested SessionLocal()."""
    db: Session = SessionLocal()
    try:
        created = propose_for_run(db, agent="logistics", findings=[_finding()])
        assert len(created) == 1
        action = created[0]
        db.refresh(action)
        assert action.mode == "AUTO"
        assert action.status in (
            "VERIFIED",
            "EXECUTED",
        ), f"expected success, got {action.status}: {action.result}"
        assert action.result and action.result.get("notification_id")
    finally:
        db.close()


def test_critical_severity_forces_manual_approval_even_for_an_auto_action_type():
    """FLAG_FOR_REVIEW is AUTO-eligible at MEDIUM/HIGH confidence, but a
    CRITICAL finding must always wait for a human, regardless of action type."""
    db: Session = SessionLocal()
    try:
        created = propose_for_run(db, agent="logistics", findings=[_finding(severity="CRITICAL")])
        assert len(created) == 1
        action = created[0]
        db.refresh(action)
        assert action.mode == "NEEDS_APPROVAL", "CRITICAL findings must not auto-execute"
        assert action.status == "PROPOSED"
        approval = db.query(Approval).filter(Approval.action_id == action.id).first()
        assert approval is not None and approval.status == "PENDING"
    finally:
        db.close()


def test_approving_a_pending_action_executes_it():
    db: Session = SessionLocal()
    try:
        created = propose_for_run(
            db,
            agent="pricing",
            findings=[
                _finding(category="SEGMENT_MARGIN", severity="CRITICAL", evidence="segment=Enterprise"),
            ],
        )
        action = created[0]
        approval = db.query(Approval).filter(Approval.action_id == action.id).first()
        assert approval is not None and approval.status == "PENDING"

        decided = decide(
            db, approval.id, approved=True, actor={"sub": None, "username": "test", "role": "SUPER_ADMIN"}
        )
        assert decided.status in (
            "VERIFIED",
            "EXECUTED",
        ), f"expected success, got {decided.status}: {decided.result}"

        db.refresh(approval)
        assert approval.status == "APPROVED"
        assert approval.decided_by == "test"
    finally:
        db.close()


def test_rejecting_a_pending_action_never_executes_it():
    db: Session = SessionLocal()
    try:
        created = propose_for_run(
            db,
            agent="marketing",
            findings=[
                _finding(category="CHURN_RISK", severity="CRITICAL", evidence="segment=At Risk"),
            ],
        )
        action = created[0]
        approval = db.query(Approval).filter(Approval.action_id == action.id).first()

        decided = decide(
            db, approval.id, approved=False, actor={"sub": None, "username": "test", "role": "SUPER_ADMIN"}
        )
        assert decided.status == "REJECTED"
        assert decided.result is None
    finally:
        db.close()


def test_duplicate_findings_are_not_reproposed():
    """De-dupe: an identical open finding for the same agent/action/title must
    not create a second AutomationAction row. Uses a fresh, run-unique title —
    this suite runs against the shared dev `orders.db` (see conftest.py), so a
    fixed title would collide with a leftover row from an earlier run."""
    db: Session = SessionLocal()
    try:
        finding = _finding(title=f"dedupe title {uuid.uuid4().hex[:10]}")
        first = propose_for_run(db, agent="logistics", findings=[finding])
        second = propose_for_run(db, agent="logistics", findings=[finding])
        assert len(first) == 1
        assert len(second) == 0
        count = (
            db.query(AutomationAction)
            .filter(AutomationAction.agent == "logistics", AutomationAction.title == finding["title"])
            .count()
        )
        assert count == 1
    finally:
        db.close()
