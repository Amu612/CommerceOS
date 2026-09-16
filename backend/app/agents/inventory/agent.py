"""
Smart Inventory Intelligence Agent.

Coordinates:
- Inventory monitoring
- Stock analysis
- Demand and sales-velocity analysis
- Reorder recommendations
- Interactive LLM-powered database queries

Interactive queries use the shared DomainAgent ReAct loop so the LLM can
dynamically select the appropriate inventory tools instead of following a
hard-coded triage -> tool -> response pipeline.
"""

import logging
import uuid
from typing import Any, ClassVar

from sqlalchemy.orm import Session

from app.agents.common_schemas import MetricCard
from app.agents.framework import DomainAgent
from app.agents.inventory.schemas import InventoryAction, InventoryAgentResponse, ToolCallRecord
from app.agents.inventory.tools import ALL_INVENTORY_TOOLS, DETECTOR_TOOLS, METRIC_TOOLS, InventoryTools
from app.database.session import SessionLocal

logger = logging.getLogger(__name__)


class InventoryWatchdogAgent(DomainAgent):
    """
    Intelligent Inventory Intelligence Agent.

    The monitoring path remains deterministic and database-backed.

    The interactive query path uses the shared DomainAgent ReAct implementation,
    allowing the configured LLM to dynamically select inventory tools based on
    the user's question, falling back to deterministic data tools when LLM is unavailable.
    """

    agent_name = "inventory"
    display_name = "Inventory Intelligence"

    persona = (
        "You are the Inventory Intelligence Agent for an e-commerce operation. "
        "Analyze inventory, stock, demand, sales velocity, reorder points, "
        "replenishment, and demand analytics across both Olist and DataCo datasets. "
        "Inventory quantities are MODELLED "
        "from observed sales demand because Olist/DataCo do not provide a real "
        "warehouse on-hand feed. Never present modelled stock as directly "
        "observed. Use the available inventory data tools whenever a question "
        "requires business data. Never invent inventory numbers."
    )

    metric_tools: ClassVar[list] = METRIC_TOOLS
    detector_tools: ClassVar[list] = DETECTOR_TOOLS
    lookup_tools: ClassVar[list] = ALL_INVENTORY_TOOLS
    recommendation_playbook: ClassVar[list] = []
    supports_live_source = False

    def _render_metric(self, tool_name: str, data: Any) -> tuple[list[MetricCard], Any | None, int]:
        cards: list[MetricCard] = []
        chart: Any | None = None
        n = 0
        if tool_name == "tool_inventory_metrics" and isinstance(data, dict):
            n = data.get("total_products", 0)
            cards = [
                MetricCard(label="Tracked Products", value=f"{n:,}", description="Catalog products in scope"),
                MetricCard(
                    label="Low Stock Items",
                    value=f"{data.get('low_stock_items', 0):,}",
                    description="Modelled stock below ROP",
                ),
                MetricCard(
                    label="Out of Stock",
                    value=f"{data.get('out_of_stock_items', 0):,}",
                    description="Zero modelled inventory",
                ),
                MetricCard(
                    label="Modelled Inventory Value",
                    value=f"${data.get('inventory_value', 0):,.2f}",
                    description="Derived from sales velocity",
                ),
            ]
        return cards, chart, n

    def __init__(self, default_threshold: int = 50):
        super().__init__()

        self.default_threshold = default_threshold
        self._last_response: InventoryAgentResponse | None = None

    def run_monitor(
        self,
        db: Session | None = None,
        threshold: int | None = None,
        admin_emails: list[str] | None = None,
    ) -> InventoryAgentResponse:
        """
        Executes a complete inventory monitoring inspection cycle.

        This path intentionally remains database-backed and deterministic.
        It is used by the monitoring/dashboard functionality rather than
        the interactive ReAct chat loop.
        """
        thresh = threshold or self.default_threshold

        close_db = False

        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            exec_id = f"exec-{uuid.uuid4().hex[:8]}"
            snap_id = f"snap-{uuid.uuid4().hex[:8]}"

            # -------------------------------------------------------------
            # 1. Query products
            # -------------------------------------------------------------
            #
            # "Low" means modelled on-hand is below the modelled reorder
            # point. Both values are derived from observed sales demand.
            #
            prods = InventoryTools.query_products(
                db,
                threshold=thresh,
                limit=40,
                low_stock_only=False,
            )

            # Full low-stock population — the same is_low test the metrics
            # card uses — so "items require restocking attention" in the
            # summary equals the Low Stock Items metric exactly. The old code
            # derived the count from the 40-row top-demand view, which
            # truncated the candidate list BEFORE the low-stock filter.
            low_stock_prods = InventoryTools.query_products(
                db,
                threshold=thresh,
                limit=100000,
                low_stock_only=True,
            )

            # -------------------------------------------------------------
            # 2. Reorder recommendations
            # -------------------------------------------------------------
            # Ranked across EVERY low-stock item so the capital-requirement
            # total covers them all; the response carries the top candidates.
            recs_all = InventoryTools.get_reorder_recommendations(
                db,
                threshold=thresh,
                limit=100000,
            )
            recs = recs_all[:15]

            # -------------------------------------------------------------
            # 3. Sales trend analysis
            # -------------------------------------------------------------
            sales_trends = InventoryTools.analyze_sales_trends(
                db,
                days=30,
                limit=12,
            )

            # -------------------------------------------------------------
            # 4. Inventory alerts
            # -------------------------------------------------------------
            #
            # Reuse the recommendations computed above so the same ranking
            # does not need to be calculated a second time.
            #
            alerts = InventoryTools.get_inventory_alerts(
                db,
                threshold=thresh,
                limit=10,
                recommendations=recs_all,
            )

            # -------------------------------------------------------------
            # 5. High-level inventory metrics
            # -------------------------------------------------------------
            metrics = InventoryTools.get_inventory_metrics(
                db,
                threshold=thresh,
            )

            # -------------------------------------------------------------
            # 6. Proactive actions
            # -------------------------------------------------------------
            #
            # These are flags only. This monitoring path does NOT execute
            # purchase orders.
            #
            actions: list[InventoryAction] = []

            for recommendation in recs[:3]:
                actions.append(
                    InventoryAction(
                        action="REORDER_FLAG_UPDATED",
                        status="FLAGGED",
                        product_id=recommendation.product_id,
                        message=(
                            f"Flagged {recommendation.name} for reorder "
                            f"(stock {recommendation.current_stock} < "
                            f"ROP {recommendation.reorder_point})."
                        ),
                    )
                )

            # -------------------------------------------------------------
            # 7. Record monitoring tool calls
            # -------------------------------------------------------------
            tool_calls = [
                ToolCallRecord(
                    tool="query_products",
                    name="query_products",
                    input={
                        "threshold": thresh,
                        "limit": 40,
                    },
                    output=(f"Returned {len(prods)} products " f"({len(low_stock_prods)} below threshold)."),
                ),
                ToolCallRecord(
                    tool="get_reorder_recommendations",
                    name="get_reorder_recommendations",
                    input={
                        "threshold": thresh,
                    },
                    output=("Computed ROP and EOQ batches for " f"{len(recs)} low-stock candidates."),
                ),
                ToolCallRecord(
                    tool="analyze_sales_trends",
                    name="analyze_sales_trends",
                    input={
                        "days": 30,
                    },
                    output=("Profiled sales velocity across " f"{len(sales_trends)} product lines."),
                ),
            ]

            # -------------------------------------------------------------
            # 8. Markdown monitoring summary
            # -------------------------------------------------------------
            summary_lines = [
                "### 🛡️ Smart Inventory Watchdog Inspection Complete",
                "",
                (
                    f"- **Catalog Overview**: Evaluated "
                    f"**{metrics.total_products:,}** products; "
                    f"**{len(low_stock_prods)}** items require "
                    "restocking attention."
                ),
                (
                    f"- **Critical Alerts**: Generated "
                    f"**{len(alerts)}** alert(s) for the current stock position."
                ),
                (
                    "- **Demand Velocity**: Sales velocity analysis completed "
                    f"for {len(sales_trends)} top-selling categories."
                ),
                (
                    "- **Capital Requirement**: Estimated reorder investment is "
                    f"**R${sum(r.estimated_cost or 0 for r in recs_all):,.2f}** "
                    f"across {len(recs_all)} purchase candidates."
                ),
                (
                    "- **Watchdog Status**: Monitoring on every inspection run "
                    "(on-demand analysis, not a background daemon)."
                ),
            ]

            output_text = "\n".join(summary_lines)

            response = InventoryAgentResponse(
                output=output_text,
                execution_id=exec_id,
                snapshot_id=snap_id,
                status="SUCCESS",
                products=prods,
                # Top 100 by demand for display — the full population is what
                # the counts/sums above are computed from.
                low_stock_products=low_stock_prods[:100],
                recommendations=recs,
                reorder_suggestions=recs,
                sales_analysis=sales_trends,
                alerts=alerts,
                actions=actions,
                metrics=metrics,
                tool_calls=tool_calls,
            )

            self._last_response = response

            return response

        finally:
            if close_db:
                db.close()

    def query(
        self,
        message: str,
        db: Session | None = None,
        history: list | None = None,
    ) -> InventoryAgentResponse:
        """
        Interactive inquiry with the Inventory Intelligence Agent.

        The previous implementation used:

            inventory_graph.invoke(...)
                -> fixed LLM triage
                -> hard-coded tool selection
                -> fixed response

        This implementation instead uses the shared DomainAgent ReAct loop:

            User question
                -> LLM
                -> dynamically select inventory tool
                -> database/tool result
                -> LLM
                -> optionally select another tool
                -> final answer

        This allows questions requiring multiple tools and calculations to be
        handled dynamically.
        """

        if not message or not message.strip():
            latest = self._last_response or self.run_monitor(db=db)
            return latest

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            chat_resp = self.chat(message=message.strip(), db=db, history=history)
            response_text = chat_resp.answer

            if not response_text:
                response_text = (
                    "The Inventory Intelligence Agent could not produce a response for that request."
                )

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
                tool_calls=latest.tool_calls,
            )

        except Exception:
            logger.exception("Inventory query failed; falling back to deterministic monitoring.")
            latest = self._last_response or self.run_monitor(db=db)
            return InventoryAgentResponse(
                output=(
                    "The Inventory Intelligence Agent encountered an error while processing the request. "
                    "The latest database-backed inventory results are shown below."
                ),
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
                tool_calls=latest.tool_calls,
            )
        finally:
            if close_db:
                db.close()


inventory_agent = InventoryWatchdogAgent()
