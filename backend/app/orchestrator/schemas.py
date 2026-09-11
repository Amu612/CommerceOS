from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DomainFinding(BaseModel):
    agent: str
    category: str
    severity: str
    title: str
    recommended_action: str
    confidence: float = 0.0
    evidence: str | None = None


class DomainSnapshot(BaseModel):
    agent: str
    display_name: str
    health: str = "HEALTHY"  # HEALTHY | NEEDS_ATTENTION | CRITICAL | NOT_ESTIMABLE | ERROR
    headline: str = ""
    confidence: float = 0.0
    metrics: List[Dict[str, Any]] = Field(default_factory=list)
    findings: List[DomainFinding] = Field(default_factory=list)
    latency_ms: float = 0.0
    error: Optional[str] = None


class SystemicFinding(BaseModel):
    title: str
    severity: str
    domains: List[str]
    explanation: str
    recommended_action: str


class Conflict(BaseModel):
    between: List[str]
    description: str
    resolution: str


class OrchestrationResult(BaseModel):
    execution_id: str
    timestamp: str
    overall_health: str
    overall_confidence: float
    summary: str
    domains: List[DomainSnapshot]
    systemic_findings: List[SystemicFinding] = Field(default_factory=list)
    conflicts: List[Conflict] = Field(default_factory=list)
    priority_actions: List[str] = Field(default_factory=list)
    kpis: Dict[str, Any] = Field(default_factory=dict)
    llm_backed: bool = False
