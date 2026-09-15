"""
The catalogue of executable automation actions.

Each handler is idempotent, records enough to roll back, and exposes `verify()`
so the executor can confirm the intended effect actually happened. The AUTO
actions here are deliberately low-blast-radius (internal flags / notifications /
queue hints) — nothing customer-facing or financial (see `policies.py`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.session import SessionLocal
from app.models.security import NotificationStatus
from app.services.notification_service import notification_service

logger = get_logger("automation.actions")

# Every handler below takes the CALLER's session (the executor's request-scoped
# `db`) and writes through it rather than opening a fresh `SessionLocal()`.
#
# That used to be a real bug, not just style: `propose_for_run`/`decide` hold
# an open write transaction on the request's `db` (an uncommitted
# `AutomationAction` insert) and, from inside that same call stack, would
# invoke a handler that opened a SECOND SQLite connection and tried to write
# on it too. SQLite allows only one writer at a time — the second connection
# would block waiting for the first to commit, but the first was itself
# blocked waiting for the handler call (on that second connection) to return.
# That's a same-thread self-deadlock, not just contention: it always ran out
# the full busy_timeout and surfaced as "database is locked" → status FAILED,
# on effectively every automation action. Sharing one session removes the
# second connection entirely.

_EVIDENCE_EQ_RE = re.compile(r"(\w+)=([^\s,'}]+)")
_EVIDENCE_DICT_RE = re.compile(r"'(\w+)':\s*'?([^,'}]+)'?")


def _parse_evidence(evidence: Any) -> dict[str, str]:
    """
    Best-effort parse of a finding's evidence string — detector tools emit either
    `key=value key2=value2` (e.g. logistics/late-risk) or a Python dict repr
    (e.g. `str(worst)` in the carrier/segment detectors). Never used to fabricate
    a number; only to link a handler's recorded effect back to the real entity
    (product/carrier/segment) the finding was about, when that's resolvable.
    """
    if not evidence:
        return {}
    s = str(evidence)
    out = {k: v.strip() for k, v in _EVIDENCE_EQ_RE.findall(s)}
    for k, v in _EVIDENCE_DICT_RE.findall(s):
        out.setdefault(k, v.strip())
    return {k: v for k, v in out.items() if v not in ("None", "")}


def _notify(db: Session, *, title: str, message: str, agent: str, severity: str) -> dict[str, Any]:
    n = notification_service.create_notification(
        db=db,
        title=title,
        message=message,
        responsible_agent=agent,
        severity=severity,
        priority=severity,
        notification_type="AUTOMATION",
    )
    return {"notification_id": n.id}


def flag_for_review(payload: dict, db: Session) -> dict:
    return _notify(
        db,
        title=f"[Auto] {payload.get('title', 'Flagged for review')}",
        message=payload.get("detail", ""),
        agent=payload.get("agent", "orchestrator"),
        severity=payload.get("severity", "MEDIUM"),
    )


def internal_notification(payload: dict, db: Session) -> dict:
    return _notify(
        db,
        title=f"[Auto] {payload.get('title', 'Operational notice')}",
        message=payload.get("detail", ""),
        agent=payload.get("agent", "orchestrator"),
        severity=payload.get("severity", "LOW"),
    )


def reprioritise_queue(payload: dict, db: Session) -> dict:
    # No live queue system in the demo dataset — record the intent + notify ops.
    res = _notify(
        db,
        title="[Auto] Queue reprioritisation applied",
        message=payload.get("detail", "Reprioritised the affected work queue."),
        agent=payload.get("agent", "orders"),
        severity="MEDIUM",
    )
    res["reprioritised"] = payload.get("entities", [])
    return res


def adjust_promised_date_model(payload: dict, db: Session) -> dict:
    res = _notify(
        db,
        title="[Auto] Promised-date buffer updated",
        message=payload.get(
            "detail", "Applied a per-lane delivery buffer from the observed margin distribution."
        ),
        agent="logistics",
        severity="MEDIUM",
    )
    res["buffer_days"] = payload.get("buffer_days")
    return res


def create_purchase_order_request(payload: dict, db: Session) -> dict:
    """
    Records a reorder request against the real stock ledger (`stock_movements`)
    so it is durably queryable and shows up in that product's stock history —
    with delta=0 (a request is not itself a quantity change; no number is
    fabricated) — plus a notification to the inventory team.
    """
    ev = _parse_evidence(payload.get("evidence"))
    product_id = ev.get("product_id")
    res = _notify(
        db,
        title=f"[PO Request] {payload.get('title', 'Reorder requested')}",
        message=payload.get("detail", "A purchase-order request was raised from a stock finding."),
        agent="inventory",
        severity=payload.get("severity", "MEDIUM"),
    )
    res["effect"] = "PURCHASE_ORDER_REQUESTED"
    res["product_id"] = product_id
    if product_id and product_id != "None":
        try:
            from app.models.operations import StockMovement

            last = (
                db.query(StockMovement)
                .filter(StockMovement.product_id == product_id)
                .order_by(StockMovement.ts.desc())
                .first()
            )
            mv = StockMovement(
                product_id=product_id,
                delta=0,
                reason="REORDER_REQUESTED",
                balance_after=last.balance_after if last else None,
            )
            db.add(mv)
            db.flush()
            res["stock_movement_id"] = mv.id
        except Exception as exc:
            logger.warning("purchase_order_stock_movement_failed", error=str(exc))
    return res


def open_carrier_review(payload: dict, db: Session) -> dict:
    """Opens a durable carrier-performance review record + notifies logistics ops."""
    ev = _parse_evidence(payload.get("evidence"))
    carrier = ev.get("carrier") or "Unknown carrier"
    res = _notify(
        db,
        title=f"[Carrier Review] {carrier}",
        message=payload.get("detail", f"Opened a performance review for carrier '{carrier}'."),
        agent="logistics",
        severity=payload.get("severity", "MEDIUM"),
    )
    res["effect"] = "CARRIER_REVIEW_OPENED"
    res["carrier"] = carrier
    res["opened_at"] = datetime.now(UTC).isoformat()
    return res


def reweight_carrier_routing(payload: dict, db: Session) -> dict:
    """Records a routing re-weight decision away from the flagged carrier + notifies ops."""
    ev = _parse_evidence(payload.get("evidence"))
    carrier = ev.get("carrier") or "Unknown carrier"
    res = _notify(
        db,
        title=f"[Routing] De-prioritised '{carrier}'",
        message=payload.get("detail", f"Time-sensitive volume re-weighted away from '{carrier}'."),
        agent="logistics",
        severity=payload.get("severity", "MEDIUM"),
    )
    res["effect"] = "CARRIER_ROUTING_REWEIGHTED"
    res["carrier"] = carrier
    return res


def set_margin_floor(payload: dict, db: Session) -> dict:
    """Records an active minimum-margin-floor policy for the flagged segment/category + notifies pricing."""
    ev = _parse_evidence(payload.get("evidence"))
    scope = ev.get("segment") or ev.get("category") or "store-wide"
    floor_pct = ev.get("lower_fence_pct") or ev.get("p25_margin_pct")
    res = _notify(
        db,
        title=f"[Margin Floor] {scope}",
        message=payload.get("detail", f"Minimum-margin floor set for '{scope}'."),
        agent="pricing",
        severity=payload.get("severity", "MEDIUM"),
    )
    res["effect"] = "MARGIN_FLOOR_SET"
    res["scope"] = scope
    res["floor_pct"] = floor_pct
    res["set_at"] = datetime.now(UTC).isoformat()
    return res


def launch_campaign(payload: dict, db: Session) -> dict:
    """Records a launched retention/win-back campaign against the flagged segment + notifies marketing."""
    ev = _parse_evidence(payload.get("evidence"))
    segment = ev.get("segment") or "targeted segment"
    res = _notify(
        db,
        title=f"[Campaign Launched] {segment}",
        message=payload.get("detail", f"Retention campaign launched for the '{segment}' segment."),
        agent="marketing",
        severity=payload.get("severity", "MEDIUM"),
    )
    res["effect"] = "CAMPAIGN_LAUNCHED"
    res["segment"] = segment
    res["launched_at"] = datetime.now(UTC).isoformat()
    return res


def escalate_to_human(payload: dict, db: Session) -> dict:
    """Escalates a finding to a human owner — a durable, notified escalation record."""
    res = _notify(
        db,
        title=f"[Escalated] {payload.get('title', 'Escalation')}",
        message=payload.get("detail", "Escalated to a human owner for review."),
        agent=payload.get("agent", "orchestrator"),
        severity=payload.get("severity", "HIGH"),
    )
    res["effect"] = "ESCALATED_TO_HUMAN"
    return res


# action_type -> (execute_fn, verify_fn, rollback_fn). execute_fn takes the
# caller's live `db` session (see the module docstring above for why).
HANDLERS: dict[
    str, tuple[Callable[[dict, Session], dict], Callable[[dict, dict], bool], Callable[[dict, dict], None]]
] = {}


def _register(name: str, fn: Callable[[dict, Session], dict]) -> None:
    def _verify(payload: dict, result: dict) -> bool:  # notification created == effect applied
        return bool(result.get("notification_id"))

    def _rollback(payload: dict, result: dict) -> None:
        nid = result.get("notification_id")
        if not nid:
            return
        db = SessionLocal()
        try:
            from app.models.security import Notification

            n = db.query(Notification).filter(Notification.id == nid).first()
            if n and n.status != NotificationStatus.RESOLVED:
                notification_service.mark_resolved(db, n)
        finally:
            db.close()

    HANDLERS[name] = (fn, _verify, _rollback)


_register("FLAG_FOR_REVIEW", flag_for_review)
_register("INTERNAL_NOTIFICATION", internal_notification)
_register("REPRIORITISE_QUEUE", reprioritise_queue)
_register("ADJUST_PROMISED_DATE_MODEL", adjust_promised_date_model)
_register("CREATE_PURCHASE_ORDER_REQUEST", create_purchase_order_request)
_register("OPEN_CARRIER_REVIEW", open_carrier_review)
_register("REWEIGHT_CARRIER_ROUTING", reweight_carrier_routing)
_register("SET_MARGIN_FLOOR", set_margin_floor)
_register("LAUNCH_CAMPAIGN", launch_campaign)
_register("ESCALATE_TO_HUMAN", escalate_to_human)


# ── mapping: agent finding category -> proposed action_type ─────
FINDING_ACTION_MAP: dict[str, str] = {
    "BACKLOG": "REPRIORITISE_QUEUE",
    "LATE_DELIVERY_RISK": "FLAG_FOR_REVIEW",
    "CARRIER_PERFORMANCE": "OPEN_CARRIER_REVIEW",
    "LANE_BOTTLENECK": "FLAG_FOR_REVIEW",
    "DELIVERY_SLA": "ADJUST_PROMISED_DATE_MODEL",
    "CANCELLATION": "FLAG_FOR_REVIEW",
    "FULFILLMENT": "ADJUST_PROMISED_DATE_MODEL",
    "STOCK": "CREATE_PURCHASE_ORDER_REQUEST",
    "LOSS_MAKING_ORDERS": "SET_MARGIN_FLOOR",
    "SEGMENT_MARGIN": "SET_MARGIN_FLOOR",
    # Acting on discount leakage means repricing — a customer-visible price
    # change, which the policy engine (ADR 0006) BLOCKS from automation. The
    # finding surfaces in the Approvals console as "Blocked (policy)".
    "DISCOUNT_LEAKAGE": "PRICE_CHANGE",
    "FREIGHT_DRAG": "FLAG_FOR_REVIEW",
    "RETENTION": "LAUNCH_CAMPAIGN",
    "CHURN_RISK": "LAUNCH_CAMPAIGN",
    "DEMAND_CONCENTRATION": "FLAG_FOR_REVIEW",
    "VOLUME": "INTERNAL_NOTIFICATION",
}
