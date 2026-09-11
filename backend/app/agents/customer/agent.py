"""
Customer Support Agent core class.

Drives the dynamic multi-agent LangGraph workflow (triage -> router ->
specialists -> supervisor) and returns a structured CustomerAgentResponse.
Data comes exclusively from the shared transaction database used by the
Orders and Inventory agents.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.agents.customer.graph import customer_graph
from app.agents.customer.schemas import (
    AGENT_MANIFEST,
    AgentTrace,
    CustomerAgentResponse,
    ToolCallRecord,
)

logger = logging.getLogger(__name__)

_EXIT_PHRASES = {"exit", "bye", "goodbye", "good bye", "good day", "quit"}


class CustomerSupportAgent:
    """Manager-style multi-agent customer support representative."""

    agent_name = "customer"

    def manifest(self):
        return AGENT_MANIFEST

    def _react(self, message: str, history: Optional[list] = None) -> Optional[CustomerAgentResponse]:
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
                    "read the live order, shipment, billing, catalog, and returns systems. For any "
                    "order-specific question, call the relevant tool with the Order ID before answering. "
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
            result = agent.invoke({"messages": prior + [HumanMessage(content=message)]}, config={"recursion_limit": 10})
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
                response=answer, final_response=answer, category="react", status="SUCCESS",
                agents_involved=["triage", "react", "supervisor"],
                traces=[AgentTrace(id="react", name="Support ReAct Agent", role="Specialist",
                                   output=answer, used_tools=tools_used)],
                tool_calls=[ToolCallRecord(tool=t, name=t) for t in tools_used],
                llm_backed=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[customer] react agent failed, falling back: {e}")
            return None

    def query(self, message: str, db: Optional[Session] = None, history: Optional[list] = None) -> CustomerAgentResponse:
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
                return CustomerAgentResponse(response=msg, final_response=msg, category="general", status="NOT_ESTIMABLE")
        except Exception:  # noqa: BLE001
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

        try:
            result = customer_graph.invoke({
                "user_input": text,
                "history": history or [],
                "intermediate_results": {},
                "traces": [],
                "tool_calls": [],
                "retry_count": 0,
                "needs_retry": False,
            })
        except Exception as e:
            logger.error(f"[CustomerSupportAgent] pipeline failed: {e}", exc_info=True)
            msg = "An error occurred while processing your request. Please try again."
            return CustomerAgentResponse(
                response=msg, final_response=msg, category="general", status="ERROR"
            )

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
        agents_involved = ["triage", "router"] + list(result.get("agents_to_call", [])) + ["supervisor"]

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
