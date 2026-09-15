"""
Orders Agent LangGraph — Production-grade ReAct agent with retry loop.
Supports Order lookup, Product search, Order Search, Analytics questions,
Shipment tracking, and Returns.
"""

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.agents.orders.schemas import OrdersAgentState
from app.agents.orders.tools import (
    tool_check_return_eligibility,
    tool_get_analytics_summary,
    tool_get_order_value_stats,
    tool_get_orders_by_period,
    tool_get_return_policy,
    tool_initiate_return,
    tool_lookup_order,
    tool_lookup_product,
    tool_search_orders,
    tool_track_shipment,
)
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

TRIAGE_PROMPT = """You are an e-commerce operations triage intelligence assistant.
Analyze the user message and extract the intent and entities as JSON:

{
    "intent": "<order_status | product_lookup | search_orders | analytics_query | order_value_query | order_period_query | shipping_tracking | return_request | return_policy | general>",
    "order_id": "<order ID if present, empty if none>",
    "product_id": "<product ID or product keyword if present, empty if none>",
    "search_query": "<search criteria or keyword if present, empty if none>",
    "tracking_number": "<tracking number if present, empty if none>",
    "customer_email": "<email if present, empty if none>",
    "period_group_by": "<'year' if the question is about years, otherwise 'month'>",
    "needs_tool": true/false
}

Rules:
- "product_lookup": user asks about a product, product ID, item details, or catalog item
- "order_status": user is asking about order status or order contents/details
- "search_orders": user wants to search orders by product, customer, or status filter
- "analytics_query": user asks about pipeline health, delay rate, fulfillment rate, backlog aging, cancellations, or forecast
- "order_value_query": user asks about average order value, AOV, total order/revenue value, or which payment method is most used/frequent
- "order_period_query": user asks how many orders were placed in a given month/year, the top months/years by order count, or to compare order volume between years
- "shipping_tracking": user asks where their package or shipment is
- "return_request": user wants to return an item or request an RMA
- "return_policy": user asks about return guidelines or refund policies
- "general": greetings, system capability questions, or chitchat

Return ONLY valid JSON without markdown formatting."""


def triage_node(state: OrdersAgentState) -> dict[str, Any]:
    """Classifies user intent and extracts order-, product-, and analytics-related entities."""
    messages = state.get("messages", [])
    last_msg = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            last_msg = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            last_msg = m.get("content", "")
            break

    if not last_msg:
        return {"intent": "general", "error_message": ""}

    lower_msg = last_msg.lower()

    # Attempt LLM triage first if an LLM key is configured
    try:
        if llm_service.is_available():
            resp = llm_service.generate_json(
                system_prompt=TRIAGE_PROMPT,
                user_message=last_msg,
            )
            if isinstance(resp, dict) and resp.get("intent"):
                intent = resp.get("intent", "general")
                order_id = str(resp.get("order_id") or "")
                product_id = str(resp.get("product_id") or "")
                search_query = str(resp.get("search_query") or "")

                # Cross-table entity disambiguation for LLM triage:
                target_check = order_id or product_id or search_query
                if target_check and target_check not in ("None", ""):
                    try:
                        from app.agents.entity_resolver import entity_resolver

                        resolved = entity_resolver.resolve_entity(target_check)
                        if resolved.get("status") == "FOUND":
                            etype = resolved.get("entity_type")
                            if etype in ("customer", "seller"):
                                intent = "search_orders"
                                search_query = target_check
                                order_id = ""
                                product_id = ""
                            elif etype == "product":
                                intent = "product_lookup"
                                product_id = target_check
                                order_id = ""
                            elif etype == "order":
                                intent = "order_status"
                                order_id = target_check
                                product_id = ""
                    except Exception:
                        pass

                return {
                    "intent": intent,
                    "order_id": order_id,
                    "product_id": product_id,
                    "search_query": search_query,
                    "tracking_number": str(resp.get("tracking_number") or ""),
                    "customer_email": str(resp.get("customer_email") or ""),
                    "period_group_by": str(resp.get("period_group_by") or "month"),
                    "error_message": "",
                }
    except Exception as e:
        logger.debug(f"[OrdersGraph] LLM triage fallback: {e}")

    # Deterministic heuristic triage fallback
    extracted_order_id = ""
    extracted_product_id = ""
    extracted_customer_id = ""
    extracted_tracking_number = ""
    search_query = ""

    # Check for tracking number patterns
    track_m = re.search(r"\b(BR-[0-9a-f]{8,32}|TRACK-[\w-]+)\b", last_msg, re.IGNORECASE)
    if track_m:
        extracted_tracking_number = track_m.group(1)

    # Check for customer ID patterns
    cust_m = re.search(
        r"(?:customer|cust)(?:\s+id|\s+#|\s*:|\s+)?\s*([0-9a-f]{8,32}|\d{1,10})", last_msg, re.IGNORECASE
    )
    if cust_m:
        extracted_customer_id = cust_m.group(1)

    # Check for product ID patterns
    prod_m = re.search(
        r"(?:product|item|prod|sku)(?:\s+id|\s+#|\s*:|\s+)?\s*([0-9a-f]{8,32}|\d{1,10})",
        last_msg,
        re.IGNORECASE,
    )
    if prod_m:
        extracted_product_id = prod_m.group(1)

    # Check for order ID patterns
    ord_m = re.search(
        r"(?:order|ord)(?:\s+id|\s+#|\s*:|\s+)?\s*([0-9a-f]{8,32}|ORD-[\w-]+|\d{1,10})",
        last_msg,
        re.IGNORECASE,
    )
    if ord_m:
        extracted_order_id = ord_m.group(1)

    # General ID fallback with Database-backed Entity Verification:
    if not extracted_order_id and not extracted_product_id and not extracted_customer_id:
        gen_m = re.search(r"\b([0-9a-f]{32}|ORD-[\w-]+)\b", last_msg, re.IGNORECASE)
        candidate = gen_m.group(1) if gen_m else None
        if not candidate:
            num_m = re.search(r"\b(\d{2,10})\b", last_msg)
            if num_m:
                candidate = num_m.group(1)

        if candidate:
            if "customer" in lower_msg or "cust" in lower_msg:
                extracted_customer_id = candidate
            elif "product" in lower_msg or "item" in lower_msg:
                extracted_product_id = candidate
            elif "order" in lower_msg or "ord" in lower_msg:
                extracted_order_id = candidate
            else:
                # Disambiguate against database records across all tables
                try:
                    from app.agents.entity_resolver import entity_resolver

                    resolved = entity_resolver.resolve_entity(candidate)
                    if resolved.get("status") == "FOUND":
                        etype = resolved.get("entity_type")
                        if etype == "customer":
                            extracted_customer_id = candidate
                        elif etype == "product":
                            extracted_product_id = candidate
                        elif etype == "order":
                            extracted_order_id = candidate
                        elif etype == "seller":
                            extracted_customer_id = candidate
                        elif etype == "review":
                            extracted_order_id = resolved.get("order_id") or candidate
                    else:
                        extracted_order_id = candidate
                except Exception:
                    extracted_order_id = candidate

    # Follow-up fallback: if this message has no order id of its own, check
    # earlier turns in the conversation (most recent first) for the last one
    # mentioned — resolves "what about its status?"-style follow-ups.
    if (
        not extracted_order_id
        and not extracted_product_id
        and not extracted_customer_id
        and len(messages) > 1
    ):
        from app.agents._shared import extract_order_id as _extract_oid

        for m in reversed(messages[:-1]):
            prior = (
                m.content
                if isinstance(m, HumanMessage)
                else (m.get("content", "") if isinstance(m, dict) else "")
            )
            oid = _extract_oid(prior or "")
            if oid:
                extracted_order_id = oid
                break

    # 1. Customer Search
    if extracted_customer_id:
        return {
            "intent": "search_orders",
            "order_id": "",
            "product_id": "",
            "search_query": extracted_customer_id,
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 2. Product Lookup — a bare mention of "product(s)"/"item(s)" (or a resolved
    # product id) routes here even without a verb like "show"/"find"/"detail".
    # Explicit order-id / order-phrasing still wins when both are present, since
    # that's answered (including item contents) by order_status below.
    if extracted_product_id or (
        not extracted_order_id
        and "order" not in lower_msg
        and any(k in lower_msg for k in ("product", "item"))
    ):
        clean_target = (
            extracted_product_id
            or last_msg.replace("show", "")
            .replace("find", "")
            .replace("product", "")
            .replace("item", "")
            .strip()
        )
        return {
            "intent": "product_lookup",
            "product_id": clean_target,
            "order_id": "",
            "search_query": clean_target,
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 2. Return Policy
    if "return policy" in lower_msg or "refund policy" in lower_msg:
        return {
            "intent": "return_policy",
            "order_id": extracted_order_id,
            "product_id": "",
            "search_query": "",
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 3. Return Request
    if "return" in lower_msg or "refund" in lower_msg or "rma" in lower_msg:
        return {
            "intent": "return_request",
            "order_id": extracted_order_id,
            "product_id": "",
            "search_query": "",
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 4. Shipment Tracking
    if "track" in lower_msg or "where is" in lower_msg or "shipping" in lower_msg:
        track_val = extracted_tracking_number or extracted_order_id
        return {
            "intent": "shipping_tracking",
            "order_id": extracted_order_id or (track_val if not track_val.startswith("BR-") else ""),
            "product_id": "",
            "search_query": "",
            "tracking_number": track_val,
            "customer_email": "",
            "error_message": "",
        }

    # 5a. Order value / payment method
    if any(
        k in lower_msg
        for k in [
            "average order value",
            "avg order value",
            " aov",
            "aov ",
            "order value",
            "payment method",
            "most used payment",
            "most frequent payment",
            "total order value",
            "total revenue",
        ]
    ):
        return {
            "intent": "order_value_query",
            "order_id": "",
            "product_id": "",
            "search_query": last_msg,
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 5b. Order counts by month/year
    if any(
        k in lower_msg
        for k in [
            "top month",
            "top year",
            "orders placed in",
            "orders were placed",
            "how many orders in",
            "orders per month",
            "orders per year",
            "orders by month",
            "orders by year",
            "compare",
            "busiest month",
            "busiest year",
        ]
    ) and ("order" in lower_msg or "compare" in lower_msg):
        return {
            "intent": "order_period_query",
            "order_id": "",
            "product_id": "",
            "search_query": last_msg,
            "tracking_number": "",
            "customer_email": "",
            "period_group_by": (
                "year"
                if any(k in lower_msg for k in ["year", "annual", "yoy"]) and "month" not in lower_msg
                else "month"
            ),
            "error_message": "",
        }

    # 5c. Pipeline Analytics (delay/fulfillment/backlog — general operational health,
    # including "how many delivered vs canceled" aggregate counts, but only when
    # no specific order id was mentioned — an order id with those words means a
    # question about THAT order, handled by order_status below instead).
    aggregate_delivered_vs_cancelled = (
        not extracted_order_id and "deliver" in lower_msg and "cancel" in lower_msg
    )
    if aggregate_delivered_vs_cancelled or any(
        k in lower_msg
        for k in [
            "delay rate",
            "fulfillment rate",
            "sla",
            "processing time",
            "delivery time",
            "how many cancel",
            "backlog",
            "forecast",
            "anomal",
            "pipeline",
            "performance",
            "metrics",
        ]
    ):
        return {
            "intent": "analytics_query",
            "order_id": "",
            "product_id": "",
            "search_query": last_msg,
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 6. Search Orders
    if ("search" in lower_msg or "find" in lower_msg) and "order" in lower_msg:
        return {
            "intent": "search_orders",
            "order_id": extracted_order_id,
            "product_id": extracted_product_id,
            "search_query": last_msg,
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    # 7. Order Status / Details
    if extracted_order_id or "order" in lower_msg or "status" in lower_msg:
        return {
            "intent": "order_status",
            "order_id": extracted_order_id,
            "product_id": "",
            "search_query": "",
            "tracking_number": "",
            "customer_email": "",
            "error_message": "",
        }

    return {
        "intent": "general",
        "order_id": extracted_order_id,
        "product_id": extracted_product_id,
        "search_query": "",
        "tracking_number": "",
        "customer_email": "",
        "error_message": "",
    }


def tool_node(state: OrdersAgentState) -> dict[str, Any]:
    """Executes relevant orders tools based on classified intent and parameters."""
    intent = state.get("intent", "general")
    order_id = state.get("order_id", "")
    product_id = state.get("product_id", "")
    search_query = state.get("search_query", "")
    retries = state.get("retry_count", 0)
    tool_results: dict[str, Any] = {}

    try:
        if intent == "product_lookup":
            target = product_id or search_query or order_id
            if target:
                res = tool_lookup_product.invoke({"product_id_or_keyword": target})
                tool_results["tool_lookup_product"] = str(res)
            else:
                tool_results["tool_lookup_product"] = (
                    "Please specify a Product ID (UUID or card ID) or category keyword to look up."
                )

        elif intent == "search_orders":
            target = search_query or product_id or order_id
            if target:
                from app.agents.entity_resolver import entity_resolver

                resolved = entity_resolver.resolve_entity(target)
                if resolved.get("status") == "FOUND" and resolved.get("entity_type") in (
                    "customer",
                    "seller",
                ):
                    tool_results["tool_search_orders"] = resolved.get("summary", "")
                else:
                    res = tool_search_orders.invoke({"query": target})
                    tool_results["tool_search_orders"] = str(res)
            else:
                tool_results["tool_search_orders"] = (
                    "Please provide search parameters (such as a Customer ID, Product ID, status, or date)."
                )

        elif intent == "analytics_query":
            res = tool_get_analytics_summary.invoke({"metric_name": "all"})
            tool_results["tool_get_analytics_summary"] = str(res)

        elif intent == "order_value_query":
            res = tool_get_order_value_stats.invoke({})
            tool_results["tool_get_order_value_stats"] = str(res)

        elif intent == "order_period_query":
            group_by = state.get("period_group_by") or "month"
            res = tool_get_orders_by_period.invoke({"group_by": group_by})
            tool_results["tool_get_orders_by_period"] = str(res)

        elif intent == "order_status":
            if order_id:
                res = tool_lookup_order.invoke({"order_id": order_id})
                tool_results["tool_lookup_order"] = str(res)
            else:
                tool_results["tool_lookup_order"] = (
                    "Please specify an Order ID (e.g. Olist UUID or integer ID) to check order status."
                )

        elif intent == "shipping_tracking":
            target = order_id or state.get("tracking_number", "")
            if target:
                res = tool_track_shipment.invoke({"tracking_number_or_order_id": target})
                tool_results["tool_track_shipment"] = str(res)
            else:
                tool_results["tool_track_shipment"] = (
                    "Please provide an Order ID or tracking code to track package milestones."
                )

        elif intent == "return_policy":
            res = tool_get_return_policy.invoke({})
            tool_results["tool_get_return_policy"] = str(res)

        elif intent == "return_request":
            if order_id:
                elig = tool_check_return_eligibility.invoke({"order_id": order_id})
                tool_results["tool_check_return_eligibility"] = str(elig)
                if "ELIGIBLE" in str(elig) and "INELIGIBLE" not in str(elig):
                    ret = tool_initiate_return.invoke(
                        {"order_id": order_id, "reason": "Customer requested return"}
                    )
                    tool_results["tool_initiate_return"] = str(ret)
            else:
                tool_results["tool_check_return_eligibility"] = (
                    "Please provide an Order ID to check return eligibility."
                )

        return {"tool_results": tool_results, "retry_count": retries + 1, "error_message": ""}

    except Exception as e:
        logger.warning(f"[OrdersGraph] tool_node execution error: {e}")
        return {
            "tool_results": {"error": str(e)},
            "retry_count": retries + 1,
            "error_message": str(e),
        }


def response_node(state: OrdersAgentState) -> dict[str, Any]:
    """Composes the final structured customer-facing or administrator response using LLM or structured formatting."""
    intent = state.get("intent", "general")
    tool_results = state.get("tool_results", {})
    error = state.get("error_message", "")
    messages = state.get("messages", [])

    user_query = ""
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            user_query = m.content
            break
        elif isinstance(m, dict) and m.get("role") == "user":
            user_query = m.get("content", "")
            break

    if error and not tool_results:
        return {
            "final_response": (
                "An operational error occurred while processing the order inquiry. "
                "Please verify the order ID or retry in a moment."
            )
        }

    if intent == "general" and not tool_results:
        return {
            "final_response": (
                "Hello! 👋 I am the **Orders Operations Intelligence Agent**.\n\n"
                "I can assist you with:\n"
                "• 📦 **Order Lookup** — Query orders by Order ID (e.g. `#58`, `#e481f51cbd...`) for items, value, status, and dates.\n"
                "• 🔍 **Product Search** — Search products by Product ID or category (e.g. `product id 1e9e8ef...` or category keyword) to see dimensions, price, and sales.\n"
                "• 🚚 **Shipment Tracking** — Real-time tracking and dispatch milestones.\n"
                "• 📊 **Pipeline Analytics** — Ask about delay rates, SLA health, processing times, backlog aging, and cancellations.\n"
                "• 🔄 **Returns & RMA** — Verify return eligibility and generate official RMAs.\n\n"
                "Please enter an Order ID, Product ID, or question to get started."
            )
        }

    # Attempt LLM answer synthesis if external LLM configured
    tool_results_text = "\n\n".join(f"[{tool}]: {res}" for tool, res in tool_results.items())
    if llm_service.is_available() and tool_results_text:
        try:
            synth_prompt = (
                "You are the Orders Operations Intelligence AI Assistant for an e-commerce platform. "
                "Answer the user's question directly, clearly, and authoritatively using the verified database query results below.\n"
                "Be CONCISE: lead with the direct answer (the number, status, or verdict asked for), "
                "show the key calculation in one line when the question asks for a computed figure, and "
                "add at most 2-3 short supporting bullets. Never pad with unrelated context.\n"
                "Structure your response cleanly with markdown, bullet points, and emojis.\n\n"
                f"User Query: {user_query}\n\n"
                f"Database Retrieval Results:\n{tool_results_text}"
            )
            llm_text = llm_service.generate_text(
                system_prompt="You are an expert e-commerce order management AI assistant.",
                user_message=synth_prompt,
            )
            if llm_text and len(llm_text.strip()) > 20:
                return {"final_response": llm_text.strip()}
        except Exception as e:
            logger.debug(f"[OrdersGraph] LLM answer synthesis fallback: {e}")

    # High-quality deterministic formatted response
    parts = []
    if intent == "product_lookup":
        parts.append("### 🔍 Product Intelligence")
    elif intent == "order_status":
        parts.append("### 📦 Order Details")
    elif intent == "search_orders":
        parts.append("### 📋 Order Search Results")
    elif intent == "analytics_query":
        parts.append("### 📊 Pipeline Intelligence")
    elif intent == "order_value_query":
        parts.append("### 💰 Order Value Intelligence")
    elif intent == "order_period_query":
        parts.append("### 📅 Order Volume by Period")
    elif intent == "shipping_tracking":
        parts.append("### 🚚 Shipment Tracking")
    elif intent == "return_request":
        parts.append("### 🔄 Return Authorization")
    elif intent == "return_policy":
        parts.append("### 📜 Store Policy")
    else:
        parts.append("### 📋 Order Intelligence")

    for _tool_name, res in tool_results.items():
        parts.append(str(res))

    return {"final_response": "\n\n".join(parts)}


def should_use_tools(state: OrdersAgentState) -> str:
    """Router deciding whether to call tools or respond directly."""
    intent = state.get("intent", "general")
    if intent == "general":
        return "response"
    return "tools"


def after_tools(state: OrdersAgentState) -> str:
    """Router after tools: checks for retryable errors.

    Only genuine transport/exception failures are retried — a legitimate
    "not found"-style answer that happens to contain the word "error" must
    flow straight to the response node (retrying identical input yields the
    identical result).
    """
    tool_results = state.get("tool_results", {})
    has_error = any(
        str(v).startswith("❌ Error") or str(v).startswith("Error:") for v in tool_results.values()
    )
    retries = state.get("retry_count", 0)

    if has_error and retries < MAX_RETRIES:
        return "retry"
    return "response"


def build_orders_graph() -> StateGraph:
    """Builds and compiles the Orders LangGraph state graph."""
    workflow = StateGraph(OrdersAgentState)

    workflow.add_node("triage", triage_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("response", response_node)

    workflow.set_entry_point("triage")

    workflow.add_conditional_edges(
        "triage",
        should_use_tools,
        {"tools": "tools", "response": "response"},
    )

    workflow.add_conditional_edges(
        "tools",
        after_tools,
        {"retry": "tools", "response": "response"},
    )

    workflow.add_edge("response", END)

    return workflow.compile()


orders_agent_graph = build_orders_graph()
