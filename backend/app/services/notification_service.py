import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from app.models.security import (
    Notification,
    NotificationStatus,
    NotificationSeverity,
    UserRole,
    AGENT_ROLE_MAP,
    _uuid,
    _utcnow,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Notification service for Orders Agent and operational domain alerts."""

    @staticmethod
    def generate_fingerprint(
        agent: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        notification_type: str = "OPERATIONAL",
        title: Optional[str] = None,
    ) -> str:
        raw = json.dumps({
            "agent": (agent or "").lower(),
            "entity_type": entity_type or "",
            "entity_id": str(entity_id) or "",
            "notification_type": notification_type,
            "title": title or "",
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def create_notification(
        cls,
        db: Session,
        title: str,
        message: str,
        responsible_agent: str,
        severity: str = "INFO",
        priority: str = "LOW",
        notification_type: str = "OPERATIONAL",
        source: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
    ) -> Notification:
        fingerprint = cls.generate_fingerprint(
            agent=responsible_agent,
            entity_type=entity_type,
            entity_id=entity_id,
            notification_type=notification_type,
            title=title,
        )

        sev_map = {
            "CRITICAL": NotificationSeverity.CRITICAL,
            "HIGH": NotificationSeverity.HIGH,
            "MEDIUM": NotificationSeverity.MEDIUM,
            "LOW": NotificationSeverity.LOW,
            "INFO": NotificationSeverity.INFO,
        }
        sev_enum = sev_map.get(str(severity).upper(), NotificationSeverity.INFO)
        target_role = AGENT_ROLE_MAP.get(responsible_agent, UserRole.ORDERS_ADMIN)

        # Check existing active notification for deduplication
        existing = db.query(Notification).filter(
            Notification.fingerprint == fingerprint,
            Notification.status.in_([
                NotificationStatus.UNREAD,
                NotificationStatus.READ,
                NotificationStatus.ACKNOWLEDGED,
            ])
        ).first()

        if existing:
            if existing.severity != sev_enum:
                existing.severity = sev_enum
                existing.message = message
                existing.execution_id = execution_id or existing.execution_id
                existing.snapshot_id = snapshot_id or existing.snapshot_id
                existing.priority = priority
                db.commit()
                db.refresh(existing)
            return existing

        notif = Notification(
            id=_uuid(),
            execution_id=execution_id,
            snapshot_id=snapshot_id,
            title=title,
            message=message,
            notification_type=notification_type,
            severity=sev_enum,
            priority=priority,
            responsible_agent=responsible_agent,
            target_role=target_role,
            source=source or "orders_agent",
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            metadata_json=metadata_json,
            fingerprint=fingerprint,
            status=NotificationStatus.UNREAD,
        )
        db.add(notif)
        db.commit()
        db.refresh(notif)
        return notif

    @staticmethod
    def mark_resolved(db: Session, notif: Notification) -> Notification:
        notif.status = NotificationStatus.RESOLVED
        notif.resolved_at = _utcnow()
        db.commit()
        db.refresh(notif)
        return notif


notification_service = NotificationService()
