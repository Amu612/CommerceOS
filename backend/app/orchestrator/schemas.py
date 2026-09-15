from __future__ import annotations

from typing import Any

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
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[DomainFinding] = Field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None


class SystemicFinding(BaseModel):
    title: str
    severity: str
    domains: list[str]
    explanation: str
    recommended_action: str


class Conflict(BaseModel):
    between: list[str]
    description: str
    resolution: str


class OrchestrationResult(BaseModel):
    execution_id: str
    timestamp: str
    overall_health: str
    overall_confidence: float
    summary: str
    domains: list[DomainSnapshot]
    systemic_findings: list[SystemicFinding] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    priority_actions: list[str] = Field(default_factory=list)
    kpis: dict[str, Any] = Field(default_factory=dict)
    llm_backed: bool = False
