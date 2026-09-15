"""
Customer Support Agent Schemas.
Data models for the dynamic multi-agent customer support workflow
(triage -> router -> specialist agents -> supervisor) and the CustomerAgentView frontend.
"""

from typing import Any

from pydantic import BaseModel, Field

# ── Agent manifest (frontend sidebar / pipeline cards) ─────────────

AGENT_MANIFEST: list[dict[str, str]] = [
    {
        "id": "triage",
        "name": "Triage Agent",
        "role": "Classifier",
        "description": "Classifies the customer query into a high-level support category.",
    },
    {
        "id": "router",
        "name": "Router Agent",
        "role": "Planner",
        "description": "Selects which specialist agents should handle the query.",
    },
    {
        "id": "context",
        "name": "Context Agent",
        "role": "Context Builder",
        "description": "Pulls the customer's order history from the transaction database for downstream agents.",
    },
    {
        "id": "support",
        "name": "Support Agent",
        "role": "Specialist",
        "description": "Handles complaints, delivery issues, and shipment tracking.",
    },
    {
        "id": "sales",
        "name": "Sales Agent",
        "role": "Specialist",
        "description": "Handles product, catalog, pricing, and purchasing questions.",
    },
    {
        "id": "billing",
        "name": "Billing Agent",
        "role": "Specialist",
        "description": "Handles invoice, charge, and payment questions using order payment records.",
    },
    {
        "id": "refund",
        "name": "Refund Agent",
        "role": "Specialist",
        "description": "Handles refund/return requests, checks eligibility, and issues RMAs.",
    },
    {
        "id": "general",
        "name": "General Agent",
        "role": "Specialist",
        "description": "Fallback for broad support questions and order status lookups.",
    },
    {
        "id": "supervisor",
        "name": "Supervisor Agent",
        "role": "Quality Controller",
        "description": "Approves the final answer or requests a retry when quality is insufficient.",
    },
]


# ── Request models ────────────────────────────────────────────────


class CustomerQueryRequest(BaseModel):
    query: str | None = None
    message: str | None = None
    history: list[dict] | None = None


# ── Response models ───────────────────────────────────────────────


class ToolCallRecord(BaseModel):
    tool: str | None = None
    name: str | None = None
    input: Any | None = None
    output: Any | None = None


class AgentTrace(BaseModel):
    id: str
    name: str
    role: str = "Specialist"
    output: str = ""
    used_tools: list[str] = Field(default_factory=list)


class CustomerAgentResponse(BaseModel):
    # `response` and `final_response` carry the same text (parity with the
    # original project which reads either key).
    response: str = ""
    final_response: str = ""
    category: str = "general"
    status: str = "SUCCESS"
    agents_involved: list[str] = Field(default_factory=list)
    traces: list[AgentTrace] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    retry_count: int = 0
    supervisor_verdict: str = "APPROVE"
    order_context: dict[str, Any] | None = None
    llm_backed: bool = False
