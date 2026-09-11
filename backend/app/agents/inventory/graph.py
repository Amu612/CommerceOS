"""
Inventory Agent LangGraph ReAct implementation and query triage.
"""
import json
import re
import logging
from typing import Dict, Any, List, Optional
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.inventory.tools import (
    ALL_INVENTORY_TOOLS,
    tool_query_inventory,
    tool_get_product_stock,
    tool_suggest_reorders,
    tool_analyze_sales_trends,
    tool_create_reorder_action,
    InventoryTools,
)
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)


class InventoryAgentState(TypedDict):
    messages: List[Any]
    intent: Optional[str]
    product_id: Optional[str]
    threshold: Optional[int]
    tool_name: Optional[str]
    tool_input: Optional[Dict[str, Any]]
    tool_result: Optional[str]
    response: Optional[str]
    error: Optional[str]


SYSTEM_CONTEXT = """You are a Smart Inventory Watchdog Agent, an intelligent 24/7 inventory guardian.
Your responsibilities:
1. Real-Time Stock Monitoring: Monitor inventory, finding products where stockQuantity < threshold.
2. Intelligent Alerting: Detect critical and low stock levels and issue alerts.
3. Predictive Reordering: Compute Reorder Point (ROP = demand * lead_time + safety_stock) and suggest EOQ batches.
4. Sales Velocity Analysis: Analyze sales trends and daily averages to forecast stock depletion.
5. Automated Action: Issue purchase orders and update reorder flags.

Provide factual, data-driven responses with markdown bullet points and clear numbers.
"""

INVENTORY_TRIAGE_PROMPT = """You are an inventory management triage assistant.
Classify the user message and extract entities as JSON:
{
    "intent": "<stock_monitoring | product_stock | reorder_suggestions | sales_analysis | reorder_action | general>",
    "product_id": "<product ID, SKU, or name if mentioned, empty otherwise>",
    "threshold": <integer threshold if specified, or 50>,
    "quantity": <reorder quantity if specified, or null>
}

Intent guide:
- "stock_monitoring": user asks for low stock products, inventory overview, or items below threshold
- "product_stock": user asks about stock, price, or details of a specific product ID / SKU
- "reorder_suggestions": user asks for reorder recommendations, ROP, suggested order quantities
- "sales_analysis": user asks about sales trends, sales velocity, or product demand
- "reorder_action": user requests to place an order, create a purchase order, or restock an item
- "general": greetings, system capabilities, or advice

Return ONLY valid JSON without markdown formatting."""


def inventory_triage_node(state: InventoryAgentState) -> Dict[str, Any]:
    """Extracts intent and entities from user message."""
    messages = state.get("messages", [])
    user_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            user_msg = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    if not user_msg:
        return {"intent": "general"}

    lower = user_msg.lower()

    # Attempt LLM triage
    if llm_service.is_available():
        try:
            parsed = llm_service.generate_json(
                system_prompt=INVENTORY_TRIAGE_PROMPT,
                user_message=user_msg,
            )
            if parsed and "intent" in parsed:
                return {
                    "intent": parsed.get("intent", "general"),
                    "product_id": parsed.get("product_id") or None,
                    "threshold": int(parsed.get("threshold") or 50),
                }
        except Exception as e:
            logger.warning(f"LLM triage failed: {e}")

    # Heuristic fallback
    hex_m = re.search(r"\b([0-9a-f]{32})\b", user_msg, re.IGNORECASE)
    sku_m = re.search(r"\b(sku-[0-9a-f]{8}|dc-\d+|\d{1,8})\b", user_msg, re.IGNORECASE)
    thresh_m = re.search(r"threshold\s*(?:of|is|:|=)?\s*(\d+)", user_msg, re.IGNORECASE)

    extracted_id = hex_m.group(1) if hex_m else (sku_m.group(1) if sku_m else None)
    extracted_thresh = int(thresh_m.group(1)) if thresh_m else 50

    # Follow-up fallback: "what about its stock?" — if this message names no
    # product, check earlier turns (most recent first) for the last one mentioned.
    if not extracted_id and len(messages) > 1:
        for m in reversed(messages[:-1]):
            prior = m.content if isinstance(m, HumanMessage) else (m.get("content", "") if isinstance(m, dict) else "")
            if not prior:
                continue
            ph = re.search(r"\b([0-9a-f]{32})\b", prior, re.IGNORECASE)
            ps = re.search(r"\b(sku-[0-9a-f]{8}|dc-\d+|\d{1,8})\b", prior, re.IGNORECASE)
            if ph or ps:
                extracted_id = ph.group(1) if ph else ps.group(1)
                break

    if any(k in lower for k in ["reorder", "purchase order", "restock", "buy", "place order"]):
        # Informational phrasing ("what's the reorder point", "ROP", "how many
        # should I order") stays read-only even when a product id is resolved
        # (including one resolved from a follow-up's history) — only explicit
        # action/confirmation language executes a real purchase order.
        if any(k in lower for k in (
            "recommend", "suggest", "candidate", "what should", "which",
            "point", "rop", "how much", "how many", "calculate", "what is", "what's",
        )):
            return {"intent": "reorder_suggestions", "threshold": extracted_thresh}
        wants_action = any(k in lower for k in (
            "place", "create", "execute", "confirm", "approve", "go ahead", "yes order", "order now",
        ))
        if extracted_id and wants_action:
            return {"intent": "reorder_action", "product_id": extracted_id}
        return {"intent": "reorder_suggestions", "threshold": extracted_thresh}

    if any(k in lower for k in ["sales", "velocity", "trend", "demand", "moving", "sold"]):
        return {"intent": "sales_analysis", "product_id": extracted_id}

    if extracted_id or any(k in lower for k in ["item", "product", "sku", "stock of", "how many"]):
        if extracted_id:
            return {"intent": "product_stock", "product_id": extracted_id}

    if any(k in lower for k in ["low stock", "stock", "monitor", "below", "inventory", "watchdog"]):
        return {"intent": "stock_monitoring", "threshold": extracted_thresh}

    return {"intent": "general"}


def inventory_tool_node(state: InventoryAgentState) -> Dict[str, Any]:
    """Executes the appropriate tool based on classified intent."""
    intent = state.get("intent") or "general"
    pid = state.get("product_id") or ""
    thresh = state.get("threshold") or 50

    tool_name = "none"
    tool_input = {}
    tool_result = ""

    try:
        if intent == "stock_monitoring":
            tool_name = "tool_query_inventory"
            tool_input = {"threshold": thresh, "low_stock_only": True}
            tool_result = tool_query_inventory.invoke(tool_input)
        elif intent == "product_stock" and pid:
            tool_name = "tool_get_product_stock"
            tool_input = {"product_id": pid}
            tool_result = tool_get_product_stock.invoke(tool_input)
        elif intent == "reorder_suggestions":
            tool_name = "tool_suggest_reorders"
            tool_input = {"threshold": thresh}
            tool_result = tool_suggest_reorders.invoke(tool_input)
        elif intent == "sales_analysis":
            tool_name = "tool_analyze_sales_trends"
            tool_input = {"product_id": pid, "days": 30}
            tool_result = tool_analyze_sales_trends.invoke(tool_input)
        elif intent == "reorder_action" and pid:
            tool_name = "tool_create_reorder_action"
            tool_input = {"product_id": pid, "quantity": 50}
            tool_result = tool_create_reorder_action.invoke(tool_input)
        else:
            tool_name = "tool_suggest_reorders"
            tool_input = {"threshold": thresh}
            tool_result = tool_suggest_reorders.invoke(tool_input)
    except Exception as e:
        logger.error(f"Error executing inventory tool {tool_name}: {e}")
        tool_result = f"Tool execution encountered an error: {e}"

    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_result": tool_result,
    }


def inventory_response_node(state: InventoryAgentState) -> Dict[str, Any]:
    """Synthesizes tool execution result and user question into clean response."""
    messages = state.get("messages", [])
    user_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            user_msg = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    tool_result = state.get("tool_result", "")
    tool_name = state.get("tool_name", "")

    # If LLM is available, synthesize a helpful response
    if llm_service.is_available():
        synth_prompt = (
            f"{SYSTEM_CONTEXT}\n\n"
            f"You ran tool '{tool_name}' which returned the following factual database records:\n"
            f"{tool_result}\n\n"
            f"Synthesize a clear, structured, and helpful response for the user's inquiry."
        )
        llm_out = llm_service.generate_text(system_prompt=synth_prompt, user_message=user_msg)
        if llm_out:
            return {"response": llm_out}

    # Deterministic fallback response
    out_lines = [
        f"**Smart Inventory Watchdog Analysis**",
        "",
        tool_result,
        "",
        "---",
        "💡 *Proactive Watchdog Tip: Safety stock levels dynamically buffer supply variance. Low-stock alerts have been refreshed.*"
    ]
    return {"response": "\n".join(out_lines)}


# ── Build LangGraph ───────────────────────────────────────────────

def build_inventory_graph():
    workflow = StateGraph(InventoryAgentState)

    workflow.add_node("triage", inventory_triage_node)
    workflow.add_node("tools", inventory_tool_node)
    workflow.add_node("response", inventory_response_node)

    workflow.set_entry_point("triage")
    workflow.add_edge("triage", "tools")
    workflow.add_edge("tools", "response")
    workflow.add_edge("response", END)

    return workflow.compile()


inventory_graph = build_inventory_graph()
