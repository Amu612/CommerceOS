"""
Operational platform tables: agent runs & findings, HITL automation actions &
approvals, orchestration decisions, and customer conversations/memory.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.olist import Base
from app.models.security import _utcnow, _uuid


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True, default=_uuid)
    agent = Column(String(40), nullable=False, index=True)
    trigger = Column(String(24), nullable=False, default="api")  # api | schedule | orchestrator
    execution_id = Column(String(60), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="SUCCESS")
    health = Column(String(24), nullable=True)
    confidence = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    latency_ms = Column(Float, nullable=True)
    token_usage = Column(JSON, nullable=True)
    cost_usd = Column(Float, nullable=True, default=0.0)
    llm_backed = Column(Boolean, nullable=False, default=False)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    findings = relationship("AgentFinding", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_agent_runs_agent_started", "agent", "started_at"),)


class AgentFinding(Base):
    __tablename__ = "agent_findings"

    id = Column(String(36), primary_key=True, default=_uuid)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    agent = Column(String(40), nullable=False, index=True)
    category = Column(String(60), nullable=False)
    severity = Column(String(16), nullable=False, default="MEDIUM")
    title = Column(String(300), nullable=False)
    what_happened = Column(Text, nullable=True)
    why_it_matters = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    data_status = Column(String(24), nullable=True, default="CALCULATED")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    run = relationship("AgentRun", back_populates="findings")


class OrchestrationDecision(Base):
    __tablename__ = "orchestration_decisions"

    id = Column(String(36), primary_key=True, default=_uuid)
    execution_id = Column(String(60), nullable=False, index=True)
    overall_health = Column(String(24), nullable=True)
    overall_confidence = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    domains = Column(JSON, nullable=True)
    systemic_findings = Column(JSON, nullable=True)
    conflicts = Column(JSON, nullable=True)
    priority_actions = Column(JSON, nullable=True)
    kpis = Column(JSON, nullable=True)
    llm_backed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class AutomationAction(Base):
    __tablename__ = "automation_actions"

    id = Column(String(36), primary_key=True, default=_uuid)
    agent = Column(String(40), nullable=False, index=True)
    action_type = Column(String(60), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    detail = Column(Text, nullable=True)
    finding_id = Column(String(36), ForeignKey("agent_findings.id", ondelete="SET NULL"), nullable=True)
    decision_id = Column(
        String(36), ForeignKey("orchestration_decisions.id", ondelete="SET NULL"), nullable=True
    )
    mode = Column(String(20), nullable=False, default="NEEDS_APPROVAL")  # AUTO | NEEDS_APPROVAL | BLOCKED
    status = Column(
        String(24), nullable=False, default="PROPOSED", index=True
    )  # PROPOSED|EXECUTED|VERIFIED|ROLLED_BACK|REJECTED|BLOCKED|FAILED
    confidence = Column(Float, nullable=True)
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    verification = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)

    approval = relationship("Approval", back_populates="action", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (Index("ix_automation_status_created", "status", "created_at"),)


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String(36), primary_key=True, default=_uuid)
    action_id = Column(
        String(36), ForeignKey("automation_actions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    required_role = Column(String(40), nullable=False, index=True)
    status = Column(
        String(16), nullable=False, default="PENDING", index=True
    )  # PENDING | APPROVED | REJECTED
    requested_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    decided_by = Column(String(100), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    reason = Column(Text, nullable=True)

    action = relationship("AutomationAction", back_populates="approval")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=_uuid)
    thread_id = Column(String(64), nullable=False, index=True)
    customer_ref = Column(String(64), nullable=True, index=True)
    channel = Column(String(24), nullable=False, default="web")
    status = Column(String(16), nullable=False, default="OPEN")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_message_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)

    messages = relationship(
        "ConversationMessage", back_populates="conversation", cascade="all, delete-orphan"
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String(36), primary_key=True, default=_uuid)
    conversation_id = Column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(16), nullable=False)  # user | assistant | agent
    content = Column(Text, nullable=False)
    agent = Column(String(40), nullable=True)
    category = Column(String(24), nullable=True)
    tool_calls = Column(JSON, nullable=True)
    llm_backed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)

    conversation = relationship("Conversation", back_populates="messages")


class CustomerMemory(Base):
    __tablename__ = "customer_memory"

    customer_ref = Column(String(64), primary_key=True)
    profile = Column(JSON, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(String(36), nullable=False, index=True)
    ts = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    delta = Column(Integer, nullable=False)
    reason = Column(String(40), nullable=False)  # SALE | REPLENISH | ADJUST | OPENING
    order_id = Column(String(36), nullable=True)
    balance_after = Column(Integer, nullable=True)
