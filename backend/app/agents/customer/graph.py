"""
Customer Support Agent — LangGraph dynamic multi-agent workflow.

Pipeline (mirrors the E-Commerce AI Customer Support Agent project):

    triage_node -> execution_node -> supervisor_node -> (retry | END)
                        ^                    |
                        └──── retry loop ────┘  (max 2 retries)

Specialist agents (context / support / sales / billing / refund / general) are
prompt personas. Each one is grounded on a tool-context string that is queried
live from the shared transaction database (orders.db) via CustomerSupportTools.

When an LLM provider is configured (llm_service), specialist + supervisor
replies are synthesised by the model. When no provider is available the graph
degrades to deterministic, database-grounded templated answers — exactly how
the Orders and Inventory graphs already behave.
"""

import logging
from typing import Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from app.agents._shared import extract_order_id_from_history
from app.agents.customer.tools import CustomerSupportTools, extract_order_id, verify_order_id
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
KNOWN_SPECIALISTS = ["context", "support", "sales", "billing", "refund", "general"]

SPECIALIST_PERSONAS = {
    "context": (
        "You are a Context Manager. Summarise the known order facts below into a short, "
        "structured brief for other support agents. Do not address the customer directly."
    ),
    "support": (
        "You are a Customer Support Specialist handling complaints, delivery problems, and "
        "shipment tracking. Acknowledge any frustration, then give a clear status and next step."
    ),
    "sales": (
        "You are a Sales Specialist for an e-commerce store. Answer product, catalog, pricing, "
        "and availability questions using the catalog data below. Be helpful and concise."
    ),
    "billing": (
        "You are a Billing Specialist. Explain charges, payment methods, and invoice details "
        "using the payment records below. Give exact amounts and dates."
    ),
    "refund": (
        "You are a Refund Specialist. State return eligibility clearly, explain the 30-day policy "
        "and 3-5 business day refund timeline, and confirm the RMA outcome if one was issued."
    ),
    "general": (
        "You are a General Support Specialist. Answer the customer's question directly and "
        "helpfully using the order/store data below."
    ),
}


class CustomerAgentState(TypedDict, total=False):
    user_input: str
    history: list[dict[str, Any]]
    category: str
    order_id: str
    product_query: str
    agents_to_call: list[str]
    intermediate_results: dict[str, str]
    traces: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    order_context: dict[str, Any] | None
    response: str
    final_response: str
    retry_count: int
    needs_retry: bool
    supervisor_verdict: str
    llm_backed: bool


# ── Triage ────────────────────────────────────────────────────────

_TRIAGE_PROMPT = """You are a customer support triage classifier.
Classify the customer message into ONE category and extract entities as JSON:
{
  "category": "<billing | refund | sales | support | general>",
  "order_id": "<order id if mentioned, else empty>",
  "product_query": "<product name / category / product id if mentioned, else empty>"
}
Category guide:
- billing: charges, invoices, payments, overcharge, subscription, installments
- refund: refund, return, money back, RMA, cancel an order
- sales: product details, price, availability, catalog, recommendations, purchasing
- support: where is my order, tracking, delivery delay, damaged item, complaint, order status
- general: greetings, policies, capabilities, anything unclear
Return ONLY valid JSON, no markdown."""


def _heuristic_triage(msg: str) -> str:
    low = (msg or "").lower()
    if any(
        k in low
        for k in ["refund", "return", "money back", "rma", "cancel my order", "cancel order", "send it back"]
    ):
        return "refund"
    if any(
        k in low
        for k in [
            "charge",
            "charged",
            "invoice",
            "payment",
            "overcharge",
            "installment",
            "subscription",
            "billed",
            "bill ",
        ]
    ):
        return "billing"
    if any(
        k in low
        for k in [
            "price",
            "cost",
            "how much",
            "in stock",
            "availability",
            "available",
            "recommend",
            "catalog",
            "buy",
            "purchase",
            "product",
            "products",
            "item ",
            "items",
            "sku",
            "do you sell",
            "do you have any",
        ]
    ):
        return "sales"
    if any(
        k in low
        for k in [
            "where is",
            "track",
            "tracking",
            "delivery",
            "delivered",
            "delayed",
            "late",
            "damaged",
            "broken",
            "arrive",
            "arrived",
            "status",
            "complaint",
            "shipping",
            "shipment",
            "package",
        ]
    ):
        return "support"
    return "general"


def triage_node(state: CustomerAgentState) -> dict[str, Any]:
    msg = state.get("user_input", "") or ""
    category = None
    order_id = ""
    product_query = ""

    if llm_service.is_available():
        try:
            parsed = llm_service.generate_json(system_prompt=_TRIAGE_PROMPT, user_message=msg)
            if isinstance(parsed, dict) and parsed.get("category"):
                category = str(parsed["category"]).strip().lower()
                order_id = str(parsed.get("order_id") or "").strip()
                product_query = str(parsed.get("product_query") or "").strip()
        except Exception as e:
            logger.debug(f"[customer] LLM triage fallback: {e}")

    if category not in ("billing", "refund", "sales", "support", "general"):
        category = _heuristic_triage(msg)

    if not order_id:
        order_id = extract_order_id(msg)
    if not order_id:
        # follow-up: "what about its status?" — reuse the last order id
        # mentioned earlier in this conversation.
        order_id = extract_order_id_from_history(msg, state.get("history"))
    if order_id and not verify_order_id(order_id):
        # keep the raw token but note it is unverified downstream
        pass

    if not product_query and category == "sales":
        product_query = msg

    return {
        "category": category,
        "order_id": order_id,
        "product_query": product_query,
        "retry_count": state.get("retry_count", 0),
        "needs_retry": False,
    }


# ── Router ────────────────────────────────────────────────────────

_ROUTER_PROMPT = """You route a customer support query to specialist agents.
Available: "context", "support", "sales", "billing", "refund", "general".
Return ONLY JSON: {"agents_to_call": ["..."], "reason": "..."}
Rules: pick the MINIMUM agents (usually 1). Include "context" first ONLY if an
order id is known and history matters. Prefer "general" if unsure."""


_INTENT_KEYWORDS = {
    "refund": ("refund", "return", "money back", "rma", "send it back", "cancel my order", "cancel order"),
    "billing": ("charge", "charged", "invoice", "payment", "overcharge", "installment", "billed", "bill "),
    "sales": (
        "price",
        "cost",
        "how much",
        "in stock",
        "availability",
        "available",
        "recommend",
        "catalog",
        "buy",
        "purchase",
        "do you sell",
        "do you have",
        "product",
        "products",
    ),
    "support": (
        "where is",
        "track",
        "tracking",
        "delivery",
        "delivered",
        "delayed",
        "late",
        "damaged",
        "broken",
        "arrive",
        "arrived",
        "status",
        "complaint",
        "shipping",
        "shipment",
        "package",
    ),
}


def _fallback_route(category: str, has_order: bool, message: str = "") -> list[str]:
    """Detect EVERY intent present in the message (multi-intent), not just the triage category."""
    low = (message or "").lower()
    hits = [name for name, kws in _INTENT_KEYWORDS.items() if any(k in low for k in kws)]
    if category in _INTENT_KEYWORDS and category not in hits:
        hits.append(category)
    if not hits:
        hits = [category if category in _INTENT_KEYWORDS else "general"]
    agents: list[str] = []
    if has_order and any(h in ("support", "refund", "billing") for h in hits):
        agents.append("context")
    agents.extend(hits)
    return agents


def _analytics_context(
    low: str, order_id: str
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None] | None:
    """
    Cross-order analytics queries — "find customers whose orders were late and
    poorly reviewed", "identify orders needing follow-up", "show my recent
    orders" — that are never about a single order id, so must be checked
    before per-specialist routing sends them somewhere order-id-shaped.
    """
    wants_late = any(k in low for k in ("late", "delay", "delayed"))
    wants_poor_review = any(
        k in low for k in ("poor review", "bad review", "low review", "review score", "poorly reviewed")
    )
    wants_followup = any(
        k in low
        for k in ("follow-up", "follow up", "followup", "need attention", "needs attention", "escalat")
    )
    wants_multi_order = any(
        k in low
        for k in (
            "find customers",
            "which customers",
            "identify orders",
            "which orders",
            "list orders",
            "list customers",
            "customers whose orders",
        )
    )
    if wants_multi_order and wants_late and wants_poor_review:
        text, recs = CustomerSupportTools.late_delivery_poor_review()
        return text, recs, None
    if wants_followup or (wants_multi_order and (wants_late or wants_poor_review)):
        text, recs = CustomerSupportTools.followup_candidates()
        return text, recs, None
    if not order_id and any(
        k in low
        for k in (
            "my recent order",
            "my previous order",
            "my orders",
            "my order history",
            "recent orders",
            "past orders",
        )
    ):
        text, recs = CustomerSupportTools.recent_orders()
        return text, recs, None
    return None


def _tool_context_for(
    agent: str, state: CustomerAgentState
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Returns (tool_context_text, tool_records, order_context_or_None)."""
    msg = state.get("user_input", "")
    order_id = state.get("order_id", "")
    low = (msg or "").lower().strip()

    analytics = _analytics_context(low, order_id)
    if analytics is not None:
        return analytics

    if agent == "context":
        return CustomerSupportTools.order_context(msg)
    if agent == "support":
        if order_id:
            text, recs = CustomerSupportTools.shipment_status(order_id)
        else:
            text, recs = CustomerSupportTools.pipeline_snapshot()
        return text, recs, None
    if agent == "sales":
        text, recs = CustomerSupportTools.product_info(state.get("product_query") or msg)
        return text, recs, None
    if agent == "billing":
        text, recs = CustomerSupportTools.billing_lookup(order_id)
        return text, recs, None
    if agent == "refund":
        text, recs = CustomerSupportTools.refund_flow(order_id, msg)
        return text, recs, None
    # general
    if not order_id and (
        len(low) <= 4
        or any(
            low.startswith(g)
            for g in ("hi", "hey", "hello", "yo", "good morning", "good afternoon", "good evening")
        )
    ):
        return (
            "Greet the customer warmly and offer help with orders, shipping, billing, refunds/returns, and products. "
            "Ask for an Order ID for anything order-specific.",
            [{"tool": "greeting", "name": "greeting", "input": {}, "output": "greeting"}],
            None,
        )
    if not order_id and any(k in low for k in ("what can you", "help", "how do you", "capabilit")):
        return (
            "Explain that you can check order status, track shipments, look up charges, check return "
            "eligibility and issue RMAs, and answer product questions — using the live store systems.",
            [{"tool": "capabilities", "name": "capabilities", "input": {}, "output": "capabilities"}],
            None,
        )
    if order_id:
        text, recs = CustomerSupportTools.order_status(order_id)
    else:
        text, recs = CustomerSupportTools.pipeline_snapshot()
    return text, recs, None


def _run_specialist(agent: str, tool_context: str, user_input: str, shared_context: str) -> tuple[str, bool]:
    persona = SPECIALIST_PERSONAS.get(agent, SPECIALIST_PERSONAS["general"])
    if llm_service.is_available():
        try:
            sys_prompt = (
                f"{persona}\n\n"
                f"Verified database facts:\n{tool_context}\n"
                + (f"\nShared context:\n{shared_context}\n" if shared_context else "")
                + "\nWrite the customer-facing reply. Be concise, factual, and friendly. "
                "Never invent order numbers, amounts, or dates."
            )
            out = llm_service.generate_text(system_prompt=sys_prompt, user_message=user_input, max_tokens=400)
            if out and out.strip():
                return out.strip(), True
        except Exception as e:
            logger.debug(f"[customer] specialist '{agent}' LLM fallback: {e}")

    # Deterministic, database-grounded fallback
    tc = tool_context.strip().lower()
    if tc == "greeting" or "greet the customer" in tc:
        return (
            "Hi! 👋 I'm your support assistant. I can help with order status, shipment tracking, "
            "billing questions, returns & refunds, and product availability. What's your Order ID, "
            "or what can I help you with?"
        ), False
    if tc == "capabilities" or "explain that you can" in tc:
        return (
            "I can: 📦 check order status & contents, 🚚 track shipments, 💳 explain charges, "
            "🔄 check return eligibility and issue an RMA, and 🛍️ answer product/price/availability "
            "questions — all from the live store systems. Give me an Order ID to get started."
        ), False
    prefix = {
        "context": "Context brief",
        "support": "Support update",
        "sales": "Sales info",
        "billing": "Billing details",
        "refund": "Refund status",
        "general": "Here's what I found",
    }.get(agent, "Here's what I found")
    return f"{prefix}: {tool_context}", False


def execution_node(state: CustomerAgentState) -> dict[str, Any]:
    category = state.get("category", "general")
    order_id = state.get("order_id", "")
    retry_count = state.get("retry_count", 0)

    # Router
    agents_to_call: list[str] = []
    if llm_service.is_available():
        try:
            router_msg = state.get("user_input", "")
            if retry_count > 0:
                router_msg = f"[RETRY {retry_count}] Previous answer was insufficient. Original: {router_msg}"
            parsed = llm_service.generate_json(
                system_prompt=_ROUTER_PROMPT
                + f"\nTriage category: {category}. Order id known: {bool(order_id)}.",
                user_message=router_msg,
            )
            if isinstance(parsed, dict):
                cand = parsed.get("agents_to_call")
                if isinstance(cand, list):
                    agents_to_call = [a for a in cand if a in KNOWN_SPECIALISTS]
        except Exception as e:
            logger.debug(f"[customer] router LLM fallback: {e}")

    if not agents_to_call:
        agents_to_call = _fallback_route(category, bool(order_id), state.get("user_input", ""))

    # Execute specialists in order
    intermediate: dict[str, str] = {}
    traces: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    llm_backed_any = False
    shared_context = ""
    order_context: dict[str, Any] | None = None

    from app.agents.customer.schemas import AGENT_MANIFEST

    role_by_id = {a["id"]: a for a in AGENT_MANIFEST}

    for agent in agents_to_call:
        tool_context, recs, ctx = _tool_context_for(agent, state)
        if ctx:
            order_context = ctx
        tool_calls.extend(recs)
        answer, was_llm = _run_specialist(agent, tool_context, state.get("user_input", ""), shared_context)
        llm_backed_any = llm_backed_any or was_llm
        intermediate[agent] = answer
        if agent == "context":
            shared_context = answer
        meta = role_by_id.get(agent, {"name": agent.title(), "role": "Specialist"})
        traces.append(
            {
                "id": agent,
                "name": meta.get("name", agent.title()),
                "role": meta.get("role", "Specialist"),
                "output": answer,
                "used_tools": [r.get("name") or r.get("tool") for r in recs],
            }
        )

    # Combine specialist replies (skip the internal context brief)
    customer_parts = [v for k, v in intermediate.items() if k != "context" and v]
    combined = (
        "\n\n".join(customer_parts) or "I'm sorry, I couldn't find enough information to help with that yet."
    )

    return {
        "agents_to_call": agents_to_call,
        "intermediate_results": intermediate,
        "traces": traces,
        "tool_calls": tool_calls,
        "order_context": order_context,
        "response": combined,
        "llm_backed": llm_backed_any,
        "needs_retry": False,
    }


# ── Supervisor ───────────────────────────────────────────────────

_SUPERVISOR_PROMPT = """You are a Supervisor doing final QA on a customer support reply.
Check correctness, completeness, clarity, and tone.
Reply with EXACTLY one of:
  APPROVE: <polished final reply>
  RETRY: <brief reason it is insufficient>
Start with 'APPROVE:' or 'RETRY:' and nothing before it."""


def supervisor_node(state: CustomerAgentState) -> dict[str, Any]:
    current = state.get("response", "")
    retry_count = state.get("retry_count", 0)

    if not current.strip():
        final = "I'm sorry, I could not generate a response. Please rephrase or provide an Order ID."
        return {
            "final_response": final,
            "response": final,
            "needs_retry": False,
            "supervisor_verdict": "APPROVE",
        }

    if llm_service.is_available():
        try:
            sup_in = (
                f"Customer message: {state.get('user_input', '')}\n\n"
                f"Draft reply:\n{current}\n\n"
                f"Retries used: {retry_count} / {MAX_RETRIES}"
            )
            out = (
                llm_service.generate_text(
                    system_prompt=_SUPERVISOR_PROMPT, user_message=sup_in, max_tokens=400
                )
                or ""
            ).strip()
            if out.upper().startswith("RETRY:") and retry_count < MAX_RETRIES:
                return {
                    "needs_retry": True,
                    "retry_count": retry_count + 1,
                    "supervisor_verdict": "RETRY",
                }
            if out.upper().startswith("APPROVE:"):
                polished = out[len("APPROVE:") :].strip() or current
            else:
                polished = out or current
            return {
                "final_response": polished,
                "response": polished,
                "needs_retry": False,
                "supervisor_verdict": "APPROVE",
            }
        except Exception as e:
            logger.debug(f"[customer] supervisor LLM fallback: {e}")

    # Deterministic approval
    return {
        "final_response": current,
        "response": current,
        "needs_retry": False,
        "supervisor_verdict": "APPROVE",
    }


def should_retry(state: CustomerAgentState) -> str:
    if state.get("needs_retry", False) and state.get("retry_count", 0) <= MAX_RETRIES:
        return "retry"
    return "end"


# ── Build graph ──────────────────────────────────────────────────


def build_customer_graph():
    workflow = StateGraph(CustomerAgentState)
    workflow.add_node("triage", triage_node)
    workflow.add_node("execution", execution_node)
    workflow.add_node("supervisor", supervisor_node)

    workflow.set_entry_point("triage")
    workflow.add_edge("triage", "execution")
    workflow.add_edge("execution", "supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        should_retry,
        {"retry": "execution", "end": END},
    )
    return workflow.compile()


customer_graph = build_customer_graph()
