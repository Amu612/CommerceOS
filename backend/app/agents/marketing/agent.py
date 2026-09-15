"""Marketing Agent — LangGraph/LangChain tool-driven."""

from __future__ import annotations

from typing import Any, ClassVar

from app.agents._shared import money
from app.agents.common_schemas import MetricCard, Recommendation
from app.agents.framework import DomainAgent
from app.agents.marketing.tools import CAMPAIGN_PLAYS, DETECTOR_TOOLS, METRIC_TOOLS


class MarketingAgent(DomainAgent):
    agent_name = "marketing"
    display_name = "Marketing Intelligence"
    persona = (
        "You are the Marketing Intelligence Agent for an e-commerce operation. You analyse customer "
        "segments (RFM), retention, category demand, and campaign opportunities."
    )
    metric_tools: ClassVar[list] = METRIC_TOOLS
    detector_tools: ClassVar[list] = DETECTOR_TOOLS
    recommendation_playbook: ClassVar[list] = [
        Recommendation(
            title="Focus budget on bringing back past buyers",
            detail="Spend less on showing ads to strangers and spend more on sending discounts to people who already bought from us.",
            expected_impact="Brings back more paying customers at a lower cost.",
            priority="AUTO",
        ),
    ]

    def _render_metric(self, tool_name: str, data: Any) -> tuple[list[MetricCard], Any | None, int]:
        cards: list[MetricCard] = []
        chart: Any | None = None
        n = 0
        if tool_name == "rfm_segments" and data.get("status") == "OK":
            n = data["total_customers"]
            by = {s["segment"]: s for s in data["segments"]}
            cards = [
                MetricCard(
                    label="Customers",
                    value=f"{data['total_customers']:,}",
                    description="unique customers up to clock T",
                ),
                MetricCard(
                    label="Repeat Rate",
                    value=f"{data['repeat_rate_pct']}%",
                    description=f"{data['repeat_customers']:,} bought 2+ times",
                    data_status="CALCULATED",
                ),
                MetricCard(
                    label="Avg Order Value", value=money(data["avg_order_value"]), data_status="CALCULATED"
                ),
                MetricCard(
                    label="Champions",
                    value=f"{by.get('Champions', {}).get('share_pct', 0)}%",
                    description=f"{by.get('Champions', {}).get('customers', 0):,} customers",
                    data_status="CALCULATED",
                ),
            ]
            chart = data["segments"]
        elif tool_name == "category_demand":
            chart = data.get("categories", [])
        elif tool_name == "customer_segment_mix":
            chart = data.get("segments", [])
        return cards, chart, n

    def _select_recommendations(self, findings):
        recs = super()._select_recommendations(findings)
        # add a segment-priority rec built from live RFM data
        try:
            from app.agents.marketing.data_layer import MarketingData
            from app.database.session import SessionLocal

            db = SessionLocal()
            try:
                rfm = MarketingData.rfm(db)
            finally:
                db.close()
            if rfm.get("status") == "OK":
                top3 = sorted(rfm["segments"], key=lambda s: -s["customers"])[:3]
                detail = "; ".join(
                    f"{s['segment']} ({s['customers']:,} buyers): {CAMPAIGN_PLAYS.get(s['segment'], 'send a special deal')}"
                    for s in top3
                )
                recs.insert(
                    0,
                    Recommendation(
                        title=f"Give special attention to '{top3[0]['segment']}' buyers",
                        detail=detail,
                        expected_impact="Brings back more repeat orders and raises revenue without spending heavily on ads.",
                        priority="HIGH" if findings else "MEDIUM",
                    ),
                )
        except Exception:
            pass
        return recs


marketing_agent = MarketingAgent()
