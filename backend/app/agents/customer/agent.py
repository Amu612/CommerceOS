"""
Customer Support Agent core class.

Drives the dynamic multi-agent LangGraph workflow (triage -> router ->
specialists -> supervisor) and returns a structured CustomerAgentResponse.
Data comes exclusively from the shared transaction database used by the
Orders and Inventory agents.
"""

import logging

from sqlalchemy.orm import Session

from app.agents.customer.graph import customer_graph
from app.agents.customer.schemas import AGENT_MANIFEST, AgentTrace, CustomerAgentResponse, ToolCallRecord

logger = logging.getLogger(__name__)

_EXIT_PHRASES = {"exit", "bye", "goodbye", "good bye", "good day", "quit"}


class CustomerSupportAgent:
    """Manager-style multi-agent customer support representative."""

    agent_name = "customer"

    def manifest(self):
        return AGENT_MANIFEST

    def _react(self, message: str, history: list | None = None) -> CustomerAgentResponse | None:
        """LangGraph ReAct agent over the customer tools. Returns None if no chat model."""
        from app.services.llm import get_chat_model

        model = get_chat_model()
        if model is None:
            return None
        try:
            from langchain_core.messages import AIMessage, HumanMessage
            from langgraph.prebuilt import create_react_agent

            from app.agents.customer.langchain_tools import CUSTOMER_TOOLS

            agent = create_react_agent(
                model,
                CUSTOMER_TOOLS,
                prompt=(
                    "You are a senior e-commerce customer support representative. You have tools that "
                    "read live orders, customers, sellers, reviews, shipments, billing, catalog, returns, "
                    "and customer experience analytics across Olist and DataCo datasets.\n"
                    "When a user provides an ID:\n"
                    "- If it is an Order ID, call lookup_order or track_shipment.\n"
                    "- If it is a Customer ID (customer_id or customer_unique_id), call lookup_customer to see their profile and purchase history.\n"
                    "- If it is a Seller ID, call lookup_seller.\n"
                    "- If it is a Review ID, call lookup_review.\n"
                    "- If the ID type is ambiguous or unknown, call resolve_unknown_id to identify its entity type and fetch linked records.\n"
                    "For analytical questions about review scores, late deliveries, CSAT, delivery experience, "
                    "seller quality, or aggregate customer experience data, ALWAYS call customer_experience_analytics "
                    "with the appropriate metric key (score_distribution, late_low_score_pct, state_low_scores, "
                    "late_1_2_pct, seller_poor_reviews, review_response_time, delay_review_hotspots, issue_rank, "
                    "delivered_vs_late, dataco_delivery_risk). Never invent statistics.\n"
                    "For questions without an order ID (e.g. 'what is the status of my order?'), explain that "
                    "you need the Order ID to look it up.\n"
                    "Be warm, concise, and specific. Never invent order numbers, amounts, dates, or "
                    "policies — only state what the tools return. If the customer wants a refund, check "
                    "eligibility first and only create an RMA if eligible."
                ),
            )
            prior: list = []
            for turn in (history or [])[-6:]:
                role = turn.get("role", "user") if isinstance(turn, dict) else "user"
                txt = turn.get("text", "") if isinstance(turn, dict) else ""
                if not txt:
                    continue
                prior.append(HumanMessage(content=txt) if role == "user" else AIMessage(content=txt))
            result = agent.invoke(
                {"messages": [*prior, HumanMessage(content=message)]}, config={"recursion_limit": 10}
            )
            msgs = result.get("messages", [])
            answer = ""
            tools_used: list[str] = []
            for m in msgs:
                for tc in getattr(m, "tool_calls", None) or []:
                    tools_used.append(tc.get("name", "tool"))
            for m in reversed(msgs):
                if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
                    answer = m.content if isinstance(m.content, str) else str(m.content)
                    break
            if not answer:
                return None
            return CustomerAgentResponse(
                response=answer,
                final_response=answer,
                category="react",
                status="SUCCESS",
                agents_involved=["triage", "react", "supervisor"],
                traces=[
                    AgentTrace(
                        id="react",
                        name="Support ReAct Agent",
                        role="Specialist",
                        output=answer,
                        used_tools=tools_used,
                    )
                ],
                tool_calls=[ToolCallRecord(tool=t, name=t) for t in tools_used],
                llm_backed=True,
            )
        except Exception as e:
            logger.warning(f"[customer] react agent failed, falling back: {e}")
            return None

    def _deterministic(self, message: str, history: list | None = None) -> CustomerAgentResponse | None:
        """
        No-LLM path for the customer agent.

        ONLY handles pure analytical CX questions (review scores, CSAT, delivery
        stats) that the customer_graph pipeline does not cover. For all
        transactional queries (refund, order status, billing, support, sales) the
        customer_graph already has keyword-based triage that works without an LLM,
        so we deliberately fall through to it.

        Returns None if the question is not a pure analytics question.
        """
        from app.agents.customer.langchain_tools import customer_experience_analytics

        low = message.lower().strip()

        # Only intercept questions that are clearly aggregate CX analytics —
        # not transactional support questions which the graph handles correctly.
        _ANALYTICS_MAP = {
            "score_distribution": [
                "review score distribution",
                "percentage of orders receiving each review",
                "score from 1 to 5",
                "each review score",
            ],
            "late_low_score_pct": [
                "late-delivered orders that received",
                "percentage of late-delivered",
            ],
            "state_low_scores": [
                "review score by customer state",
                "five states with the lowest scores",
                "states with lowest average review",
            ],
            "late_1_2_pct": [
                "experienced a late delivery and subsequently",
                "late delivery and subsequently gave",
            ],
            "seller_poor_reviews": [
                "sellers have the highest proportion of poor reviews",
                "sellers with at least 20 reviews",
            ],
            "review_response_time": [
                "time between review creation and review answer",
                "average time between review creation",
            ],
            "delay_review_hotspots": [
                "delivery delays and poor reviews occur together",
                "delays and poor reviews occur together",
            ],
            "issue_rank": [
                "cx issues ranked",
                "customer experience issues ranked",
            ],
            "delivered_vs_late": [
                "average review score for delivered-on-time vs",
                "on-time vs late average score",
            ],
            "dataco_delivery_risk": [
                "dataco delivery risk",
                "dataco late delivery",
            ],
        }
        for m_name, kws in _ANALYTICS_MAP.items():
            if any(kw in low for kw in kws):
                try:
                    ans = customer_experience_analytics.invoke({"metric": m_name})
                    return CustomerAgentResponse(
                        response=ans,
                        final_response=ans,
                        category="analytics",
                        status="SUCCESS",
                        agents_involved=["triage", "deterministic", "supervisor"],
                        llm_backed=False,
                    )
                except Exception:
                    pass

        # Fall through — let customer_graph handle everything else.
        # The graph has keyword-based triage for refund / support / billing / sales
        # that works correctly even without an LLM.
        return None

    def query(
        self, message: str, db: Session | None = None, history: list | None = None
    ) -> CustomerAgentResponse:
        """
        Runs one full pass of the customer support pipeline.

        `db` is accepted for API/test symmetry with the other agents; the graph
        nodes open their own short-lived sessions (like the Orders/Inventory
        LangChain tools) so the pipeline stays stateless. `history` (the last
        few {role, text} turns) lets the triage node resolve a follow-up
        question to an order id mentioned earlier in the conversation.
        """
        text = (message or "").strip()
        if not text:
            return CustomerAgentResponse(
                response="Please enter a question or an Order ID.",
                final_response="Please enter a question or an Order ID.",
                category="general",
                status="EMPTY",
            )

        try:
            from app.services.data_source_service import data_source_service

            if data_source_service.is_live():
                # This agent's tools only know Olist/DataCo — don't silently
                # answer from historic data while Live is selected.
                msg = (
                    "The live Shopify data source doesn't have customer-support data yet "
                    "(this agent still only reads the historic Olist/DataCo dataset) — "
                    "switch back to Historic to use it."
                )
                return CustomerAgentResponse(
                    response=msg, final_response=msg, category="general", status="NOT_ESTIMABLE"
                )
        except Exception:
            pass

        if text.lower() in _EXIT_PHRASES:
            farewell = "Thank you for contacting support. Have a great day!"
            return CustomerAgentResponse(
                response=farewell,
                final_response=farewell,
                category="general",
                agents_involved=["general"],
            )

        # Preferred path: a real LangGraph ReAct agent over the customer tools.
        react = self._react(text, history=history)
        if react is not None:
            return react

        # ── Deterministic fallback when LLM is unavailable ──────────────────
        # When the LLM/Groq is rate-limited or unavailable, skip customer_graph
        # (which would also try to call the LLM and hang for 60s retrying).
        # Instead, try to answer directly from tools.
        deterministic = self._deterministic(text, history=history)
        if deterministic is not None:
            return deterministic

        try:
            result = customer_graph.invoke(
                {
                    "user_input": text,
                    "history": history or [],
                    "intermediate_results": {},
                    "traces": [],
                    "tool_calls": [],
                    "retry_count": 0,
                    "needs_retry": False,
                }
            )
        except Exception as e:
            logger.error(f"[CustomerSupportAgent] pipeline failed: {e}", exc_info=True)
            msg = "An error occurred while processing your request. Please try again."
            return CustomerAgentResponse(response=msg, final_response=msg, category="general", status="ERROR")

        final = result.get("final_response") or result.get("response") or "No response generated."
        traces = [
            AgentTrace(
                id=t.get("id", "agent"),
                name=t.get("name", "Agent"),
                role=t.get("role", "Specialist"),
                output=t.get("output", ""),
                used_tools=[x for x in (t.get("used_tools") or []) if x],
            )
            for t in result.get("traces", [])
        ]
        tool_calls = [
            ToolCallRecord(
                tool=r.get("tool"),
                name=r.get("name"),
                input=r.get("input"),
                output=r.get("output"),
            )
            for r in result.get("tool_calls", [])
        ]
        agents_involved = ["triage", "router", *list(result.get("agents_to_call", [])), "supervisor"]

        return CustomerAgentResponse(
            response=final,
            final_response=final,
            category=result.get("category", "general"),
            status="SUCCESS",
            agents_involved=agents_involved,
            traces=traces,
            tool_calls=tool_calls,
            retry_count=result.get("retry_count", 0),
            supervisor_verdict=result.get("supervisor_verdict", "APPROVE"),
            order_context=result.get("order_context"),
            llm_backed=bool(result.get("llm_backed", False)),
        )


customer_support_agent = CustomerSupportAgent()
