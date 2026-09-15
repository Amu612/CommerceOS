"""Shared response schemas for the logistics / pricing / marketing agents + orchestrator."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Finding(BaseModel):
    category: str
    severity: str = "MEDIUM"  # LOW | MEDIUM | HIGH | CRITICAL
    title: str
    what_happened: str
    why_it_matters: str
    recommended_action: str
    evidence: str = ""
    confidence: float = 0.0
    data_status: str = "CALCULATED"
    sample_count: int | None = None


class Recommendation(BaseModel):
    title: str
    detail: str
    expected_impact: str = ""
    priority: str = "MEDIUM"
    requires_approval: bool = True


class MetricCard(BaseModel):
    label: str
    value: Any
    unit: str | None = None
    description: str | None = None
    data_status: str = "OBSERVED"


class ToolCall(BaseModel):
    tool: str
    input: Any | None = None
    output: Any | None = None


class AgentAnalysisOutput(BaseModel):
    agent: str
    execution_id: str
    timestamp: str
    status: str = "SUCCESS"
    confidence: float = 0.0
    health: str = "HEALTHY"  # HEALTHY | NEEDS_ATTENTION | CRITICAL | NOT_ESTIMABLE
    summary: str = ""
    metrics: list[MetricCard] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    charts: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    llm_backed: bool = False
    not_estimable_reason: str | None = None


class AgentQueryResponse(BaseModel):
    agent: str
    intent: str = "general"
    answer: str
    data: dict[str, Any] | None = None
    llm_backed: bool = False
    success: bool = True
