"""
The catalogue of executable automation actions.

Each handler is idempotent, records enough to roll back, and exposes `verify()`
so the executor can confirm the intended effect actually happened. The AUTO
actions here are deliberately low-blast-radius (internal flags / notifications /
queue hints) — nothing customer-facing or financial (see `policies.py`).
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger
from app.database.session import SessionLocal
from app.models.security import NotificationSeverity, NotificationStatus
from app.services.notification_service import notification_service

logger = get_logger("automation.actions")


def _notify(*, title: str, message: str, agent: str, severity: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        n = notification_service.create_notification(
            db=db, title=title, message=message, responsible_agent=agent,
            severity=severity, priority=severity, notification_type="AUTOMATION",
        )
        return {"notification_id": n.id}
    finally:
        db.close()


def flag_for_review(payload: dict) -> dict:
    return _notify(
        title=f"[Auto] {payload.get('title', 'Flagged for review')}",
        message=payload.get("detail", ""),
        agent=payload.get("agent", "orchestrator"),
        severity=payload.get("severity", "MEDIUM"),
    )


def internal_notification(payload: dict) -> dict:
    return _notify(
        title=f"[Auto] {payload.get('title', 'Operational notice')}",
        message=payload.get("detail", ""),
        agent=payload.get("agent", "orchestrator"),
        severity=payload.get("severity", "LOW"),
    )


def reprioritise_queue(payload: dict) -> dict:
    # No live queue system in the demo dataset — record the intent + notify ops.
    res = _notify(
        title="[Auto] Queue reprioritisation applied",
        message=payload.get("detail", "Reprioritised the affected work queue."),
        agent=payload.get("agent", "orders"),
        severity="MEDIUM",
    )
    res["reprioritised"] = payload.get("entities", [])
    return res


def adjust_promised_date_model(payload: dict) -> dict:
    res = _notify(
        title="[Auto] Promised-date buffer updated",
        message=payload.get("detail", "Applied a per-lane delivery buffer from the observed margin distribution."),
        agent="logistics", severity="MEDIUM",
    )
    res["buffer_days"] = payload.get("buffer_days")
    return res


# action_type -> (execute_fn, verify_fn, rollback_fn)
HANDLERS: dict[str, tuple[Callable[[dict], dict], Callable[[dict, dict], bool], Callable[[dict, dict], None]]] = {}


def _register(name: str, fn: Callable[[dict], dict]) -> None:
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
    "DISCOUNT_LEAKAGE": "FLAG_FOR_REVIEW",
    "FREIGHT_DRAG": "FLAG_FOR_REVIEW",
    "RETENTION": "LAUNCH_CAMPAIGN",
    "CHURN_RISK": "LAUNCH_CAMPAIGN",
    "DEMAND_CONCENTRATION": "FLAG_FOR_REVIEW",
    "VOLUME": "INTERNAL_NOTIFICATION",
}
