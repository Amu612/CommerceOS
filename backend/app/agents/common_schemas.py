"""Shared response schemas for the logistics / pricing / marketing agents + orchestrator."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    sample_count: Optional[int] = None


class Recommendation(BaseModel):
    title: str
    detail: str
    expected_impact: str = ""
    priority: str = "MEDIUM"
    requires_approval: bool = True


class MetricCard(BaseModel):
    label: str
    value: Any
    unit: Optional[str] = None
    description: Optional[str] = None
    data_status: str = "OBSERVED"


class ToolCall(BaseModel):
    tool: str
    input: Optional[Any] = None
    output: Optional[Any] = None


class AgentAnalysisOutput(BaseModel):
    agent: str
    execution_id: str
    timestamp: str
    status: str = "SUCCESS"
    confidence: float = 0.0
    health: str = "HEALTHY"  # HEALTHY | NEEDS_ATTENTION | CRITICAL | NOT_ESTIMABLE
    summary: str = ""
    metrics: List[MetricCard] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    charts: Dict[str, Any] = Field(default_factory=dict)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    llm_backed: bool = False
    not_estimable_reason: Optional[str] = None


class AgentQueryResponse(BaseModel):
    agent: str
    intent: str = "general"
    answer: str
    data: Optional[Dict[str, Any]] = None
    llm_backed: bool = False
    success: bool = True
