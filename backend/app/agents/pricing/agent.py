"""Pricing & Margin Agent — LangGraph/LangChain tool-driven."""
from __future__ import annotations

from typing import Any, Optional

from app.agents._shared import money
from app.agents.common_schemas import MetricCard, Recommendation
from app.agents.framework import DomainAgent
from app.agents.pricing.tools import DETECTOR_TOOLS, LOOKUP_TOOLS, METRIC_TOOLS


class PricingAgent(DomainAgent):
    agent_name = "pricing"
    display_name = "Pricing & Margin Intelligence"
    persona = (
        "You are the Pricing & Margin Intelligence Agent for an e-commerce operation. You analyse "
        "gross margin, discount effectiveness, loss-making orders, and category price positioning."
    )
    metric_tools = METRIC_TOOLS
    detector_tools = DETECTOR_TOOLS
    lookup_tools = LOOKUP_TOOLS
    recommendation_playbook = [
        Recommendation(
            title="Set a minimum price floor so no order loses money",
            detail="Stop or review any checkout where the total price does not cover product and shipping costs.",
            expected_impact="Stops money losses right away without hurting regular sales.",
            priority="AUTO",
        ),
        Recommendation(
            title="Send coupons to specific buyers instead of store-wide sales",
            detail="Give special discounts only to select customers instead of lowering prices for everyone on the site.",
            expected_impact="Saves money on unnecessary discounts while keeping sales strong.",
            priority="MEDIUM",
        ),
    ]

    def _render_metric(self, tool_name: str, data: Any) -> tuple[list[MetricCard], Optional[Any], int]:
        cards: list[MetricCard] = []
        chart: Optional[Any] = None
        n = 0
        if tool_name == "margin_overview" and data.get("status") == "OK":
            n = data["sample_count"]
            cards = [
                MetricCard(label="Blended Margin", value=f"{data['blended_margin_pct']}%",
                           description=f"{money(data['total_profit'])} on {money(data['total_revenue'])}", data_status="CALCULATED"),
                MetricCard(label="Median Order Margin", value=f"{data['median_order_margin_pct']}%",
                           description=f"P25 {data['p25_margin_pct']}%", data_status="CALCULATED"),
                MetricCard(label="Loss-Making Orders", value=f"{data['loss_making_rate_pct']}%",
                           description=f"{data['loss_making_orders']:,} orders below zero margin", data_status="CALCULATED"),
            ]
        elif tool_name == "margin_by_segment":
            segs = data.get("segments", [])
            n = sum(s["orders"] for s in segs)
            cards = [MetricCard(label="Segments Analysed", value=len(segs), description="by market")]
            chart = segs
        elif tool_name == "category_price_benchmarks":
            chart = data.get("categories", [])
        elif tool_name == "discount_leakage" and data.get("available"):
            chart = data.get("categories", [])
        return cards, chart, n


pricing_agent = PricingAgent()
