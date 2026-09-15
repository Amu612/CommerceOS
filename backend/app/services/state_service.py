import logging
from datetime import UTC, datetime
from typing import Any

from app.services.event_bus import OperationalEvent, event_bus

logger = logging.getLogger(__name__)


class BusinessStateService:
    """Incremental Business State Aggregator derived strictly from order event streams."""

    def __init__(self):
        self.reset_state()
        event_bus.subscribe(self.handle_event)

    def reset_state(self) -> None:
        """Resets state metrics back to zero."""
        self.total_orders: int = 0
        self.orders_processed: int = 0
        self.delivered_orders: int = 0
        self.cancelled_orders: int = 0
        self.delayed_orders: int = 0
        self.pending_orders: int = 0

        self.merchandise_revenue: float = 0.0
        self.freight_total: float = 0.0
        self.payment_volume: float = 0.0

        self.total_items_sold: int = 0
        self.sources_seen: set = set()
        self.sim_start_date: datetime | None = None
        self.sim_current_date: datetime | None = None
        self.recent_events: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.reset_state()

    def handle_event(self, event: OperationalEvent) -> None:
        """Incremental state update handler triggered on every published event."""
        payload = event.payload or {}
        event_type = event.event_type

        evt_dt = event.event_timestamp
        if evt_dt is not None and getattr(evt_dt, "tzinfo", None) is None:
            evt_dt = evt_dt.replace(tzinfo=UTC)

        if self.sim_start_date is None or (evt_dt and evt_dt < self.sim_start_date):
            self.sim_start_date = evt_dt
        if self.sim_current_date is None or (evt_dt and evt_dt > self.sim_current_date):
            self.sim_current_date = evt_dt

        src = str(payload.get("source", "olist")).lower()
        self.sources_seen.add(src)

        # Log to recent events feed
        event_entry = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "event_timestamp": event.event_timestamp.isoformat() if event.event_timestamp else None,
            "source_record_id": event.source_record_id,
            "details": payload,
        }
        self.recent_events.insert(0, event_entry)
        if len(self.recent_events) > 50:
            self.recent_events.pop()

        status = str(payload.get("status") or "").lower()

        if event_type == "ORDER_CREATED":
            self.total_orders += 1
            if status in ("canceled", "cancelled", "suspected_fraud", "unavailable"):
                self.cancelled_orders += 1
            elif status in ("delivered", "complete", "closed"):
                self.delivered_orders += 1
                self.orders_processed += 1
            else:
                self.pending_orders += 1
                self.orders_processed += 1

            if payload.get("is_delayed") or payload.get("late_delivery_risk") == 1:
                self.delayed_orders += 1

            self.merchandise_revenue += float(payload.get("merchandise_revenue", 0.0))
            self.freight_total += float(payload.get("freight_value", 0.0))
            self.payment_volume += float(payload.get("payment_total", 0.0))
            self.total_items_sold += int(payload.get("item_count", 1))

        elif event_type == "ORDER_DELIVERED":
            if self.pending_orders > 0:
                self.pending_orders -= 1
            self.delivered_orders += 1
            if payload.get("is_delayed"):
                self.delayed_orders += 1

        elif event_type == "ORDER_CANCELLED":
            if self.pending_orders > 0:
                self.pending_orders -= 1
            self.cancelled_orders += 1

    def get_overview(self) -> dict[str, Any]:
        """Returns standard business overview metrics."""
        fulfillment_rate = (
            round((self.delivered_orders / self.total_orders) * 100.0, 2) if self.total_orders > 0 else 0.0
        )
        cancellation_rate = (
            round((self.cancelled_orders / self.total_orders) * 100.0, 2) if self.total_orders > 0 else 0.0
        )
        return {
            "total_orders": self.total_orders,
            "orders_processed": self.orders_processed,
            "pending_orders": max(0, self.total_orders - self.delivered_orders - self.cancelled_orders),
            "delivered_orders": self.delivered_orders,
            "cancelled_orders": self.cancelled_orders,
            "delayed_orders": self.delayed_orders,
            "fulfillment_rate_pct": fulfillment_rate,
            "cancellation_rate_pct": cancellation_rate,
            "merchandise_revenue": round(self.merchandise_revenue, 2),
            "total_items_sold": self.total_items_sold,
            "sim_current_date": self.sim_current_date.isoformat() if self.sim_current_date else None,
            "recent_events_count": len(self.recent_events),
        }


state_service = BusinessStateService()
