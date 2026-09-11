import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OperationalEvent(BaseModel):
    """Deterministic Operational Event derived from historical records or live streams."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_timestamp: datetime
    source_record_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    source: str = "database"
    execution_id: Optional[str] = None
    actor_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = self.model_dump()
        data["event_timestamp"] = self.event_timestamp.isoformat()
        data["created_at"] = self.created_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OperationalEvent":
        if isinstance(data.get("event_timestamp"), str):
            data["event_timestamp"] = datetime.fromisoformat(data["event_timestamp"])
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return cls(**data)


class EventBus:
    """In-memory event bus with subscriber listener dispatch."""

    CHANNEL_NAME = "commerceos_events"

    def __init__(self):
        self._listeners: List[Callable[[OperationalEvent], None]] = []

    def subscribe(self, callback: Callable[[OperationalEvent], None]) -> None:
        """Register a callback subscriber to receive all published events."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[OperationalEvent], None]) -> None:
        """Unregister a subscriber."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def publish(self, event: OperationalEvent) -> None:
        """Publishes an event to all registered in-memory subscribers."""
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception as e:
                logger.error(f"[EventBus] Error in event listener {listener}: {e}", exc_info=True)


event_bus = EventBus()


# ── Platform event fan-out (Redis pub/sub → WebSocket) ─────────────
def publish_event(channel: str, payload: Dict[str, Any]) -> None:
    """
    Publish a lightweight platform event (agent run, notification, ingestion tick,
    automation decision, …) to Redis so every API instance's WebSocket clients get it.
    Falls back to the in-process listeners when Redis is off.
    """
    import json
    from datetime import datetime, timezone

    envelope = {
        "channel": channel,
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    try:
        from app.core.redis import EVENTS_CHANNEL, get_redis

        client = get_redis()
        if client is not None:
            client.publish(EVENTS_CHANNEL, json.dumps(envelope))
            return
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[events] redis publish failed: {exc}")

    # In-process fallback
    for cb in list(_local_platform_listeners):
        try:
            cb(envelope)
        except Exception:  # noqa: BLE001
            pass


_local_platform_listeners: List[Callable[[Dict[str, Any]], None]] = []


def subscribe_platform_local(cb: Callable[[Dict[str, Any]], None]) -> None:
    if cb not in _local_platform_listeners:
        _local_platform_listeners.append(cb)
