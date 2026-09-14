"""Logistics Agent — LangGraph/LangChain tool-driven."""
from __future__ import annotations

from typing import Any, Optional

from app.agents.common_schemas import MetricCard, Recommendation
from app.agents.framework import DomainAgent
from app.agents.logistics.tools import DETECTOR_TOOLS, LOOKUP_TOOLS, METRIC_TOOLS


class LogisticsAgent(DomainAgent):
    agent_name = "logistics"
    display_name = "Logistics Intelligence"
    persona = (
        "You are the Logistics Intelligence Agent for an e-commerce operation. You analyse carrier "
        "and lane performance, transit-time distributions, delivery SLA, and late-delivery risk."
    )
    metric_tools = METRIC_TOOLS
    detector_tools = DETECTOR_TOOLS
    lookup_tools = LOOKUP_TOOLS
    recommendation_playbook = [
        Recommendation(
            title="Switch packages to faster delivery companies",
            detail="Send urgent packages with the fastest and most dependable delivery partners.",
            expected_impact="Fewer late deliveries and happier customers.",
            priority="AUTO",
        ),
        Recommendation(
            title="Give realistic delivery dates for each city",
            detail="Give far-away places more delivery days and nearby places fewer days instead of one guess for everyone.",
            expected_impact="Keep our delivery promises and reduce customer complaints.",
            priority="MEDIUM",
        ),
    ]

    def _render_metric(self, tool_name: str, data: Any) -> tuple[list[MetricCard], Optional[Any], int]:
        cards: list[MetricCard] = []
        chart: Optional[Any] = None
        n = 0
        if tool_name == "logistics_overview":
            risk, sla = data.get("late_risk", {}), data.get("olist_sla", {})
            n = risk.get("total_shipments", 0)
            cards = [
                MetricCard(label="Shipments Observed", value=f"{risk.get('total_shipments', 0):,}", description="DataCo lanes up to clock T"),
                MetricCard(label="Late-Delivery Risk", value=f"{risk.get('at_risk_rate_pct', 0)}%",
                           description=f"{risk.get('at_risk', 0):,} shipments flagged", data_status="CALCULATED"),
                MetricCard(label="On-Time Rate (Olist)",
                           value=f"{sla.get('on_time_rate_pct', 0)}%" if sla.get("status") == "OK" else "N/A",
                           description="Delivered on/before estimate", data_status="CALCULATED"),
            ]
        elif tool_name == "transit_time_distribution" and data.get("status") == "OK":
            cards = [MetricCard(label="Median Transit", value=f"{data['median_days']}d",
                                description=f"Slowest 10% take {data['p90_days']}d+ · outliers beyond {data['outlier_fence_days']}d", data_status="CALCULATED")]
            chart = data
        elif tool_name == "carrier_scorecard":
            chart = data.get("carriers", [])
        elif tool_name == "lane_scorecard":
            chart = data.get("lanes", [])
        return cards, chart, n


logistics_agent = LogisticsAgent()
