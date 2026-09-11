"""
Shared LangGraph agent framework for the domain agents.

Every domain agent is:
  - a set of LangChain `@tool` functions (the real, data-driven computation:
    SQL aggregates + `app.intelligence` statistics — the LLM never does math), split into
      * "metric" tools    -> return numeric snapshots
      * "detector" tools   -> return a structured Finding (or None) from empirical fences
  - `analyze()`  : runs every detector + metric tool, assembles an AgentAnalysisOutput
  - `chat()`     : a real LangGraph ReAct agent (`create_react_agent`) when a chat
                   model is configured; otherwise a deterministic tool-router that
                   still executes the LangChain tools and renders their real output.

Nothing about the findings is hardcoded: thresholds come from the observed
distribution (Tukey fences / modified z-score / binomial SE) inside the tools.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.agents._shared import extract_order_id_from_history, money
from app.agents.common_schemas import (
    AgentAnalysisOutput,
    AgentQueryResponse,
    Finding,
    MetricCard,
    Recommendation,
)
from app.core.logging import get_logger
from app.intelligence.confidence.calculator import ConfidenceCalculator
from app.services.llm import get_chat_model, get_llm

logger = get_logger("agent.framework")


class DomainAgent:
    """Base class. Subclasses provide: agent_name, display_name, persona, and the tool lists."""

    agent_name: str = "domain"
    display_name: str = "Domain"
    persona: str = "You are a domain intelligence agent."

    #: tools whose JSON output becomes metric cards / chart data
    metric_tools: list[BaseTool] = []
    #: tools that each return {"finding": {...}} | {"finding": None} from empirical analysis
    detector_tools: list[BaseTool] = []
    #: extra tools available to the chat ReAct loop (lookups etc.)
    lookup_tools: list[BaseTool] = []
    #: static, always-applicable playbook recommendations (still selected by live findings)
    recommendation_playbook: list[Recommendation] = []
    #: Olist/DataCo (historic) is the only source this agent's tools query
    #: today. Rather than silently showing stale historic numbers while "Live"
    #: is selected, agents that haven't been made Shopify-aware report
    #: NOT_ESTIMABLE honestly instead — never blending the two sources.
    supports_live_source: bool = False

    # ── analysis ────────────────────────────────────────────────
    def analyze(self, db=None) -> AgentAnalysisOutput:  # noqa: ARG002 - tools self-manage sessions
        exec_id = f"EXEC-{self.agent_name[:3].upper()}-{uuid.uuid4().hex[:6].upper()}"
        now = datetime.now(timezone.utc).isoformat()

        if not self.supports_live_source:
            live_block = self._live_source_block(exec_id, now)
            if live_block is not None:
                return live_block

        metrics: list[MetricCard] = []
        charts: dict[str, Any] = {}
        tool_calls: list[dict] = []
        sample = 0

        for t in self.metric_tools:
            try:
                raw = t.invoke({})
                data = _as_obj(raw)
                tool_calls.append({"tool": t.name, "output": data})
                cards, chart, n = self._render_metric(t.name, data)
                metrics.extend(cards)
                if chart is not None:
                    charts[t.name] = chart
                sample = max(sample, n)
            except Exception as exc:  # noqa: BLE001
                logger.warning("metric_tool_failed", tool=t.name, error=str(exc))

        findings: list[Finding] = []
        for t in self.detector_tools:
            try:
                raw = _as_obj(t.invoke({}))
                tool_calls.append({"tool": t.name, "output": raw})
                f = raw.get("finding") if isinstance(raw, dict) else None
                if f:
                    findings.append(Finding(**f))
            except Exception as exc:  # noqa: BLE001
                logger.warning("detector_tool_failed", tool=t.name, error=str(exc))

        if sample == 0 and not metrics:
            return AgentAnalysisOutput(
                agent=self.agent_name, execution_id=exec_id, timestamp=now,
                status="NOT_ESTIMABLE", health="NOT_ESTIMABLE",
                not_estimable_reason="No records observed up to the current simulated clock. Start the Data Ingestion Engine (Start Stream / Step +50).",
                summary=f"No {self.agent_name} data yet — start the ingestion stream.",
                tool_calls=[_tc(x) for x in tool_calls],
            )

        confidence = ConfidenceCalculator.evaluate(sample_size=max(sample, 1), data_quality=0.82).confidence_score
        severities = {f.severity for f in findings}
        health = (
            "CRITICAL" if "CRITICAL" in severities
            else "NEEDS_ATTENTION" if findings
            else "HEALTHY"
        )
        recs = self._select_recommendations(findings)
        summary = self._summary(metrics, findings)

        return AgentAnalysisOutput(
            agent=self.agent_name, execution_id=exec_id, timestamp=now,
            confidence=round(confidence, 3), health=health, summary=summary,
            metrics=metrics, findings=findings, recommendations=recs,
            charts=charts, tool_calls=[_tc(x) for x in tool_calls],
            llm_backed=False,
        )

    # API-compat aliases
    def run_analysis(self, db=None, **_: Any) -> AgentAnalysisOutput:
        return self.analyze(db=db)

    def query(self, message: str, db=None, history: Optional[list] = None, **_: Any) -> AgentQueryResponse:
        return self.chat(message, db=db, history=history)

    def _live_source_block(self, exec_id: str, now: str) -> Optional[AgentAnalysisOutput]:
        """None when historic (or the agent is live-aware); a clean NOT_ESTIMABLE
        output when Live is active and this agent's tools only know Olist/DataCo."""
        try:
            from app.services.data_source_service import data_source_service

            if not data_source_service.is_live():
                return None
        except Exception:  # noqa: BLE001
            return None
        reason = (
            f"The live Shopify data source doesn't have {self.agent_name} analytics yet "
            "(this agent still only reads the historic Olist/DataCo dataset) — switch back "
            "to Historic to see it, or ask for this to be added."
        )
        return AgentAnalysisOutput(
            agent=self.agent_name, execution_id=exec_id, timestamp=now,
            status="NOT_ESTIMABLE", health="NOT_ESTIMABLE",
            not_estimable_reason=reason,
            summary=f"No live-source data for {self.display_name} yet.",
        )

    # subclasses override these three
    def _render_metric(self, tool_name: str, data: Any) -> tuple[list[MetricCard], Optional[Any], int]:
        return [], data if isinstance(data, list) else None, _sample_of(data)

    def _select_recommendations(self, findings: list[Finding]) -> list[Recommendation]:
        if not findings:
            return []
        prio = "HIGH" if any(f.severity in ("HIGH", "CRITICAL") for f in findings) else "MEDIUM"
        out = []
        for r in self.recommendation_playbook:
            out.append(r.model_copy(update={"priority": prio if r.priority == "AUTO" else r.priority}))
        return out

    def _summary(self, metrics: list[MetricCard], findings: list[Finding]) -> str:
        head = "; ".join(f"{m.label} {m.value}" for m in metrics[:4])
        tail = f" — {len(findings)} finding(s) need attention." if findings else " — all dimensions within empirical baseline."
        return (head + tail).strip()

    # ── chat ───────────────────────────────────────────────────
    def chat(self, message: str, db=None, history: Optional[list] = None) -> AgentQueryResponse:  # noqa: ARG002
        analysis = self.analyze(db=db)
        if not self.supports_live_source and analysis.not_estimable_reason and "live Shopify data source" in analysis.not_estimable_reason:
            # Don't fall through to _react_chat/_deterministic_chat — both would
            # invoke tools that only know Olist/DataCo, silently answering from
            # the historic dataset while "Live" is selected.
            return AgentQueryResponse(
                agent=self.agent_name, intent="not_estimable", answer=analysis.not_estimable_reason,
                data={"summary": analysis.summary}, llm_backed=False,
            )
        context = {
            "summary": analysis.summary,
            "health": analysis.health,
            "metrics": [m.model_dump() for m in analysis.metrics],
            "findings": [f.model_dump() for f in analysis.findings],
            "recommendations": [r.model_dump() for r in analysis.recommendations],
            "charts": {k: v for k, v in analysis.charts.items()},
        }
        model = get_chat_model()
        if model is not None:
            try:
                answer = self._react_chat(model, message, history=history)
                if answer:
                    return AgentQueryResponse(agent=self.agent_name, intent="react", answer=answer,
                                              data=context, llm_backed=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("react_chat_failed", agent=self.agent_name, error=str(exc))

        return AgentQueryResponse(
            agent=self.agent_name, intent="deterministic",
            answer=self._deterministic_chat(message, analysis, history=history), data=context, llm_backed=False,
        )

    def _react_chat(self, model, message: str, history: Optional[list] = None) -> str:
        from langgraph.prebuilt import create_react_agent

        tools = list(self.metric_tools) + list(self.lookup_tools)
        agent = create_react_agent(
            model,
            tools,
            prompt=(
                f"{self.persona}\n\nYou have tools that return live, verified data from the "
                "e-commerce database — including any order-id lookup tools. Call the tools you "
                "need, then answer the user's question concisely and quantitatively. Never invent "
                "numbers, entities, or categories — only use tool output. If the tools don't cover "
                "the question, say so. The conversation history is provided for context on "
                "follow-up questions (e.g. resolving 'it'/'that order' to a previously-mentioned id)."
            ),
        )
        messages: list[Any] = []
        for turn in (history or [])[-6:]:
            role = turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", "user")
            text = turn.get("text") if isinstance(turn, dict) else getattr(turn, "text", "")
            if not text:
                continue
            messages.append(HumanMessage(content=text) if role == "user" else AIMessage(content=text))
        messages.append(HumanMessage(content=message))
        result = agent.invoke(
            {"messages": messages},
            config={"recursion_limit": 8},
        )
        msgs = result.get("messages", [])
        for m in reversed(msgs):
            if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
                return m.content if isinstance(m.content, str) else str(m.content)
        return ""

    def _deterministic_chat(self, message: str, analysis: AgentAnalysisOutput, history: Optional[list] = None) -> str:
        """
        No-LLM path: still runs the LangChain tools. If the question (or, failing
        that, a recent turn in `history`) references an order id and this agent
        has order-id-aware lookup tools, answers that specific order first.
        Otherwise picks the tool(s) whose name/description best matches the
        question, executes them, and renders their real output.
        """
        low = (message or "").lower().strip()
        lines = [f"**{self.display_name}** — {analysis.summary}", ""]

        if not low or any(k in low for k in ("hello", "hi ", "hey ", "help", "what can you", "capabilit")):
            lines.append("I can pull: " + ", ".join(t.name.replace("_", " ") for t in self.metric_tools) + ".")
            for m in analysis.metrics:
                lines.append(f"- {m.label}: {m.value}" + (f" — {m.description}" if m.description else ""))
            return "\n".join(lines)

        rendered = False

        # entity-specific: an order id in this message or a recent follow-up turn
        order_id = extract_order_id_from_history(message, history) if self.lookup_tools else ""
        if order_id:
            for t in self.lookup_tools:
                try:
                    out = _as_obj(t.invoke({"order_id": order_id}))
                    if isinstance(out, dict) and out.get("status") == "NOT_FOUND":
                        continue
                    lines.append(f"**{_titleize(t.name)}** (order #{order_id}):")
                    lines.extend(_render_rows(out))
                    rendered = True
                    break
                except Exception:  # noqa: BLE001
                    continue

        # score tools by keyword overlap with the question
        if not rendered:
            scored: list[tuple[int, BaseTool]] = []
            toks = {w for w in _re_words(low) if len(w) > 2}
            for t in list(self.metric_tools):
                hay = f"{t.name} {t.description}".lower()
                score = sum(1 for w in toks if w in hay)
                if score:
                    scored.append((score, t))
            scored.sort(key=lambda s: -s[0])

            for _, t in scored[:2]:
                try:
                    out = _as_obj(t.invoke({}))
                    lines.append(f"**{_titleize(t.name)}**:")
                    lines.extend(_render_rows(out))
                    rendered = True
                except Exception:  # noqa: BLE001
                    continue

        if not rendered:
            if any(k in low for k in ("why", "explain", "cause", "reason", "matter")):
                for f in analysis.findings[:4]:
                    lines.append(f"- **[{f.severity}] {f.title}** — {f.what_happened} {f.why_it_matters}")
            elif any(k in low for k in ("recommend", "should", "action", "fix", "advice", "do next")):
                for r in analysis.recommendations:
                    lines.append(f"- **{r.title}** ({r.priority}) — {r.detail}")
            else:
                for f in analysis.findings[:3]:
                    lines.append(f"- **[{f.severity}] {f.title}** — {f.recommended_action}")
                if not analysis.findings:
                    for m in analysis.metrics:
                        lines.append(f"- {m.label}: {m.value}")

        lines += ["", "*This is a data-grounded deterministic answer.*"]
        return "\n".join(lines)


# ── helpers ────────────────────────────────────────────────────
def _as_obj(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {"text": raw}
    return raw


def _tc(x: dict) -> Any:
    from app.agents.common_schemas import ToolCall

    return ToolCall(tool=x.get("tool", "?"), input=x.get("input"), output=x.get("output"))


def _sample_of(data: Any) -> int:
    if isinstance(data, dict):
        for k in ("sample_count", "count", "total", "shipments", "sample"):
            if isinstance(data.get(k), (int, float)):
                return int(data[k])
    if isinstance(data, list):
        return len(data)
    return 0


def _re_words(s: str) -> list[str]:
    import re

    return re.findall(r"[a-z][a-z\-]+", s)


_ACRONYMS = {"rfm", "sla", "rop", "eoq", "roi", "csat", "ltv", "cac", "sku"}


def _titleize(name: str) -> str:
    words = name.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in words)


def _field_label(k: str) -> str:
    return _titleize(k.strip())


def _field_value(k: str, v: Any) -> str:
    kl = k.lower()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        if "pct" in kl or "percent" in kl:
            return f"{v}%"
        if any(t in kl for t in ("revenue", "profit", "price", "cost", "value", "total", "freight", "discount", "margin_amount")):
            return money(v)
        return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}"
    return str(v) if v is not None else "—"


def _render_row(row: dict) -> str:
    parts = [f"{_field_label(k)}: {_field_value(k, v)}" for k, v in row.items() if v is not None and not isinstance(v, (dict, list))]
    return "- " + " · ".join(parts)


def _render_rows(out: Any) -> list[str]:
    if isinstance(out, dict):
        # a list-of-rows under some key?
        for v in out.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [_render_row(row) for row in v[:8]]
        return [f"- {_field_label(k)}: {_field_value(k, v)}" for k, v in out.items() if not isinstance(v, (dict, list))][:12]
    if isinstance(out, list):
        return [_render_row(row) if isinstance(row, dict) else f"- {row}" for row in out[:8]]
    return [f"- {out}"]
