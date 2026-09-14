"""
Smart Inventory Watchdog Agent core class.
Coordinates real-time stock monitoring, intelligent alerting,
predictive reordering, sales velocity analysis, and interactive chat.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session
from langchain_core.messages import AIMessage, HumanMessage

from app.database.session import SessionLocal
from app.agents.inventory.schemas import (
    InventoryAgentResponse,
    InventoryProduct,
    ReorderRecommendation,
    SalesAnalysis,
    InventoryAlert,
    InventoryAction,
    ToolCallRecord,
    InventoryAgentMetrics,
)
from app.agents.inventory.tools import InventoryTools
from app.agents.inventory.graph import inventory_graph

logger = logging.getLogger(__name__)


class InventoryWatchdogAgent:
    """
    Intelligent 24/7 Smart Inventory Watchdog Agent.
    """

    def __init__(self, default_threshold: int = 50):
        self.default_threshold = default_threshold
        self._last_response: Optional[InventoryAgentResponse] = None

    def run_monitor(
        self,
        db: Optional[Session] = None,
        threshold: Optional[int] = None,
        admin_emails: Optional[List[str]] = None,
    ) -> InventoryAgentResponse:
        """
        Executes a complete inventory monitoring inspection cycle.
        """
        thresh = threshold or self.default_threshold
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            exec_id = f"exec-{uuid.uuid4().hex[:8]}"
            snap_id = f"snap-{uuid.uuid4().hex[:8]}"

            # 1. Query products (ranked by real demand). "Low" = modelled on-hand below
            #    the modelled reorder point — both derived from observed sales.
            prods = InventoryTools.query_products(db, threshold=thresh, limit=40, low_stock_only=False)
            low_stock_prods = [p for p in prods if p.reorder_required]

            # 2. Reorder recommendations
            recs = InventoryTools.get_reorder_recommendations(db, threshold=thresh, limit=15)

            # 3. Sales trend analysis
            sales_trends = InventoryTools.analyze_sales_trends(db, days=30, limit=12)

            # 4. Inventory alerts (reuse the recommendations computed above — same
            #    underlying ranking, no second pass over the catalogue)
            alerts = InventoryTools.get_inventory_alerts(db, threshold=thresh, limit=10, recommendations=recs)

            # 5. High-level metrics
            metrics = InventoryTools.get_inventory_metrics(db, threshold=thresh)

            # 6. Proactive actions
            actions: List[InventoryAction] = []
            for r in recs[:3]:
                actions.append(
                    InventoryAction(
                        action="REORDER_FLAG_UPDATED",
                        status="FLAGGED",
                        product_id=r.product_id,
                        message=f"Flagged {r.name} for reorder (stock {r.current_stock} < ROP {r.reorder_point}).",
                    )
                )

            # 7. Record tool calls for frontend display
            tool_calls = [
                ToolCallRecord(
                    tool="query_products",
                    name="query_products",
                    input={"threshold": thresh, "limit": 40},
                    output=f"Returned {len(prods)} products ({len(low_stock_prods)} below threshold).",
                ),
                ToolCallRecord(
                    tool="get_reorder_recommendations",
                    name="get_reorder_recommendations",
                    input={"threshold": thresh},
                    output=f"Computed ROP and EOQ batches for {len(recs)} low-stock candidates.",
                ),
                ToolCallRecord(
                    tool="analyze_sales_trends",
                    name="analyze_sales_trends",
                    input={"days": 30},
                    output=f"Profiled sales velocity across {len(sales_trends)} product lines.",
                ),
            ]

            # 8. Markdown summary output
            summary_lines = [
                f"### 🛡️ Smart Inventory Watchdog Inspection Complete",
                f"",
                f"- **Catalog Overview**: Evaluated **{metrics.total_products:,}** products; **{len(low_stock_prods)}** items require restocking attention.",
                f"- **Critical Alerts**: Generated **{len(alerts)}** alert(s) for the current stock position.",
                f"- **Demand Velocity**: Sales velocity analysis completed for {len(sales_trends)} top-selling categories.",
                f"- **Capital Requirement**: Estimated reorder investment is **R${sum(r.estimated_cost or 0 for r in recs):,.2f}** across {len(recs)} purchase candidates.",
                f"- **Watchdog Status**: Monitoring on every inspection run (on-demand analysis, not a background daemon).",
            ]
            output_text = "\n".join(summary_lines)

            res = InventoryAgentResponse(
                output=output_text,
                execution_id=exec_id,
                snapshot_id=snap_id,
                status="SUCCESS",
                products=prods,
                low_stock_products=low_stock_prods,
                recommendations=recs,
                reorder_suggestions=recs,
                sales_analysis=sales_trends,
                alerts=alerts,
                actions=actions,
                metrics=metrics,
                tool_calls=tool_calls,
            )

            self._last_response = res
            return res

        finally:
            if close_db:
                db.close()

    def query(self, message: str, db: Optional[Session] = None, history: Optional[list] = None) -> InventoryAgentResponse:
        """
        Interactive inquiry or chat with the Inventory Watchdog Agent.
        """
        prior: list = []
        for turn in (history or [])[-6:]:
            role = turn.get("role", "user") if isinstance(turn, dict) else "user"
            text = turn.get("text", "") if isinstance(turn, dict) else ""
            if not text:
                continue
            prior.append(HumanMessage(content=text) if role == "user" else AIMessage(content=text))
        graph_input = {
            "messages": prior + [HumanMessage(content=message)],
            "threshold": self.default_threshold,
        }

        # Run LangGraph pipeline
        result = inventory_graph.invoke(graph_input)
        response_text = result.get("response") or "The Inventory Agent processed your request."
        tool_name = result.get("tool_name")
        tool_input = result.get("tool_input")
        tool_result = result.get("tool_result")

        tool_calls = []
        if tool_name and tool_name != "none":
            tool_calls.append(
                ToolCallRecord(
                    tool=tool_name,
                    name=tool_name,
                    input=tool_input,
                    output=tool_result,
                )
            )

        # Build response payload
        latest = self._last_response or self.run_monitor(db=db)

        return InventoryAgentResponse(
            output=response_text,
            execution_id=latest.execution_id,
            snapshot_id=latest.snapshot_id,
            status="SUCCESS",
            products=latest.products,
            low_stock_products=latest.low_stock_products,
            recommendations=latest.recommendations,
            reorder_suggestions=latest.reorder_suggestions,
            sales_analysis=latest.sales_analysis,
            alerts=latest.alerts,
            actions=latest.actions,
            metrics=latest.metrics,
            tool_calls=tool_calls or latest.tool_calls,
        )


inventory_agent = InventoryWatchdogAgent()
