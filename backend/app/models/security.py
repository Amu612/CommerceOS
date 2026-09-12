import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, Float, Index, Enum as SAEnum, JSON
from app.models.olist import Base


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    INVENTORY_ADMIN = "INVENTORY_ADMIN"
    ORDERS_ADMIN = "ORDERS_ADMIN"
    CUSTOMER_SUPPORT_ADMIN = "CUSTOMER_SUPPORT_ADMIN"
    PRICING_ADMIN = "PRICING_ADMIN"
    MARKETING_ADMIN = "MARKETING_ADMIN"
    LOGISTICS_ADMIN = "LOGISTICS_ADMIN"


class NotificationStatus(str, enum.Enum):
    UNREAD = "UNREAD"
    READ = "READ"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class NotificationSeverity(str, enum.Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# The canonical agent key -> its owning admin role. "customer" is the only
# spelling any route, the frontend, or a DB row (`AutomationAction.agent`,
# `AgentRun.agent`) ever actually uses — a "customer_support" alias used to
# sit here too, mapping to the same role, and silently broke the reverse
# lookup below (ROLE_AGENTS[CUSTOMER_SUPPORT_ADMIN] resolved to whichever
# entry happened to be inserted last, i.e. the alias, not "customer") —
# support_admin's own automation/run-history self-scoping filtered on an
# agent key nothing was ever tagged with and came back permanently empty.
# Keep this map one role -> one agent key; a second key for the same role
# reintroduces exactly that bug.
AGENT_ROLE_MAP = {
    "inventory": UserRole.INVENTORY_ADMIN,
    "orders": UserRole.ORDERS_ADMIN,
    "customer": UserRole.CUSTOMER_SUPPORT_ADMIN,
    "pricing": UserRole.PRICING_ADMIN,
    "marketing": UserRole.MARKETING_ADMIN,
    "logistics": UserRole.LOGISTICS_ADMIN,
}

ROLE_AGENTS = {v: k for k, v in AGENT_ROLE_MAP.items()}
assert len(ROLE_AGENTS) == len(AGENT_ROLE_MAP), (
    "AGENT_ROLE_MAP must be a one-to-one mapping — two agent keys pointing at "
    "the same role would make ROLE_AGENTS (the reverse lookup every "
    "self-scoping RBAC check uses) silently resolve to whichever key was "
    "inserted last, not necessarily the real one."
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole, name="user_role_enum", create_constraint=True), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String(36), primary_key=True, default=_uuid)
    execution_id = Column(String(50), nullable=True, index=True)
    snapshot_id = Column(String(50), nullable=True, index=True)
    title = Column(String(500), nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String(100), nullable=False, default="OPERATIONAL")
    severity = Column(SAEnum(NotificationSeverity, name="notification_severity_enum", create_constraint=True), nullable=False, default=NotificationSeverity.INFO)
    priority = Column(String(20), nullable=False, default="LOW")
    responsible_agent = Column(String(50), nullable=True, index=True)
    target_role = Column(SAEnum(UserRole, name="notification_target_role_enum", create_constraint=True), nullable=True, index=True)
    target_user_id = Column(String(36), nullable=True, index=True)
    source = Column(String(100), nullable=True)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(String(255), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    fingerprint = Column(String(64), unique=False, nullable=True, index=True)
    status = Column(SAEnum(NotificationStatus, name="notification_status_enum", create_constraint=True), nullable=False, default=NotificationStatus.UNREAD)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_notifications_status_role", "status", "target_role"),
        Index("ix_notifications_agent_status", "responsible_agent", "status"),
        Index("ix_notifications_fingerprint_status", "fingerprint", "status"),
    )


class AgentPrediction(Base):
    __tablename__ = "agent_predictions"

    id = Column(String(36), primary_key=True, default=_uuid)
    execution_id = Column(String(50), nullable=False, index=True)
    snapshot_id = Column(String(50), nullable=False, index=True)
    agent_id = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    prediction = Column(JSON, nullable=False)
    confidence = Column(Float, nullable=False, default=0.85)
    status = Column(String(20), nullable=False, default="SUCCESS")
    metrics_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        Index("ix_agent_predictions_exec_agent", "execution_id", "agent_id"),
    )


class AuditLog(Base):
    """Append-only audit trail. One row per authenticated mutating request + explicit security events."""

    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=_uuid)
    action = Column(String(150), nullable=False, index=True)
    actor_id = Column(String(36), nullable=True, index=True)
    actor_username = Column(String(100), nullable=True)
    actor_role = Column(String(50), nullable=True)
    target_type = Column(String(64), nullable=True)
    target_id = Column(String(255), nullable=True)
    status_code = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    request_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
