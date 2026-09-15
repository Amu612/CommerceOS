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

import contextvars
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from app.agents._shared import money
from app.agents.common_schemas import (
    AgentAnalysisOutput,
    AgentQueryResponse,
    Finding,
    MetricCard,
    Recommendation,
)
from app.core.logging import get_logger
from app.intelligence.confidence.calculator import ConfidenceCalculator
from app.services.llm import get_chat_model

logger = get_logger("agent.framework")


# ── side-effect confirmation gate ─────────────────────────────────────────
# Tools that change state (create a purchase order, open an RMA) may only run
# after the USER — never the model — explicitly affirmed in the current
# conversation. The chat loop records the user's own words here before the
# ReAct agent runs; the side-effecting tools re-check them at execution time,
# so a model cannot "self-confirm" by passing confirm=True.
_recent_user_messages: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "recent_user_messages",
    default=(),
)

_CONFIRM_RE = re.compile(
    r"^\s*(?:please\s+)?(?:yes|yeah|yep|yup|sure|ok(?:ay)?|confirm(?:ed)?|approved?|go ahead|proceed)\b",
    re.IGNORECASE,
)


def register_user_message(text: str) -> None:
    """Record the user's own words for this turn so side-effecting tools can
    verify explicit human confirmation."""
    if not text:
        return
    current = _recent_user_messages.get()
    _recent_user_messages.set((text,) + current[-2:])


def user_explicitly_confirmed() -> bool:
    """True only when the user's most recent message begins with an explicit
    affirmative ('confirm', 'yes — go ahead', 'proceed', …)."""
    msgs = _recent_user_messages.get()
    return bool(msgs) and bool(_CONFIRM_RE.match(msgs[0]))


@tool
def calculator(expression: str) -> dict:
    """Evaluate an arithmetic expression exactly (e.g. "(12450 / 38000) * 100").
    Use this for ANY calculation on tool numbers — percentages, differences,
    ratios, averages — so the result is mathematically correct, never estimated."""
    import ast as _ast
    import operator as _op

    _SAFE_OPS = {
        _ast.Add: _op.add,
        _ast.Sub: _op.sub,
        _ast.Mult: _op.mul,
        _ast.Div: _op.truediv,
        _ast.Pow: _op.pow,
        _ast.Mod: _op.mod,
        _ast.FloorDiv: _op.floordiv,
        _ast.USub: _op.neg,
        _ast.UAdd: _op.pos,
    }

    def _eval(node):
        if isinstance(node, _ast.Constant) and isinstance(node.value, int | float):
            return node.value
        if isinstance(node, _ast.BinOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    try:
        return {"result": _eval(_ast.parse(expression, mode="eval").body)}
    except Exception as exc:
        return {"error": f"cannot evaluate '{expression}': {exc}"}


# Reused across every chat turn (stateless).
_CALCULATOR = [calculator]


class DomainAgent:
    """Base class. Subclasses provide: agent_name, display_name, persona, and the tool lists."""

    agent_name: str = "domain"
    display_name: str = "Domain"
    persona: str = "You are a domain intelligence agent."

    #: tools whose JSON output becomes metric cards / chart data
    metric_tools: ClassVar[list[BaseTool]] = []
    #: tools that each return {"finding": {...}} | {"finding": None} from empirical analysis
    detector_tools: ClassVar[list[BaseTool]] = []
    #: extra tools available to the chat ReAct loop (lookups etc.)
    lookup_tools: ClassVar[list[BaseTool]] = []
    #: static, always-applicable playbook recommendations (still selected by live findings)
    recommendation_playbook: ClassVar[list[Recommendation]] = []
    #: Olist/DataCo (historic) is the only source this agent's tools query
    #: today. Rather than silently showing stale historic numbers while "Live"
    #: is selected, agents that haven't been made Shopify-aware report
    #: NOT_ESTIMABLE honestly instead — never blending the two sources.
    supports_live_source: bool = False

    # ── analysis ────────────────────────────────────────────────
    def analyze(self, db=None) -> AgentAnalysisOutput:
        exec_id = f"EXEC-{self.agent_name[:3].upper()}-{uuid.uuid4().hex[:6].upper()}"
        now = datetime.now(UTC).isoformat()

        if not self.supports_live_source:
            live_block = self._live_source_block(exec_id, now)
            if live_block is not None:
                return live_block

        metrics: list[MetricCard] = []
        charts: dict[str, Any] = {}
        tool_calls: list[dict] = []
        sample = 0
        metric_ok = metric_total = 0

        for t in self.metric_tools:
            metric_total += 1
            try:
                raw = t.invoke({})
                data = _as_obj(raw)
                tool_calls.append({"tool": t.name, "output": data})
                cards, chart, n = self._render_metric(t.name, data)
                metrics.extend(cards)
                if chart is not None:
                    charts[t.name] = chart
                sample = max(sample, n)
                metric_ok += 1
            except Exception as exc:
                logger.warning("metric_tool_failed", tool=t.name, error=str(exc))

        findings: list[Finding] = []
        detector_ok = detector_total = 0
        for t in self.detector_tools:
            detector_total += 1
            try:
                raw = _as_obj(t.invoke({}))
                tool_calls.append({"tool": t.name, "output": raw})
                f = raw.get("finding") if isinstance(raw, dict) else None
                if f:
                    findings.append(Finding(**f))
                detector_ok += 1
            except Exception as exc:
                logger.warning("detector_tool_failed", tool=t.name, error=str(exc))

        if sample == 0 and not metrics:
            return AgentAnalysisOutput(
                agent=self.agent_name,
                execution_id=exec_id,
                timestamp=now,
                status="NOT_ESTIMABLE",
                health="NOT_ESTIMABLE",
                not_estimable_reason="No records observed up to the current simulated clock. Start the Data Ingestion Engine (Start Stream / Step +50).",
                summary=f"No {self.agent_name} data yet — start the ingestion stream.",
                tool_calls=[_tc(x) for x in tool_calls],
            )

        # Data quality is measured, not assumed: the fraction of analysis tools
        # that actually succeeded against the database this run.
        ran = metric_ok + detector_ok
        total = metric_total + detector_total
        data_quality = round(ran / total, 3) if total else 0.5

        # If every detector blew up we cannot claim "all dimensions healthy" —
        # that would be a silent failure, not a healthy system.
        if detector_total and detector_ok == 0:
            return AgentAnalysisOutput(
                agent=self.agent_name,
                execution_id=exec_id,
                timestamp=now,
                status="NOT_ESTIMABLE",
                health="NOT_ESTIMABLE",
                not_estimable_reason=f"All {self.agent_name} detectors failed this run — results would be silently incomplete, so no health verdict is issued.",
                summary=f"{self.agent_name.title()} detectors unavailable.",
                metrics=metrics,
                charts=charts,
                tool_calls=[_tc(x) for x in tool_calls],
            )

        confidence = ConfidenceCalculator.evaluate(
            sample_size=max(sample, 1), data_quality=data_quality
        ).confidence_score

        # Findings whose confidence was not derived from their own sample size
        # (i.e. any hardcoded constant in a tool) are recalibrated here from the
        # number of records the finding is actually grounded in.
        for f in findings:
            if f.sample_count:
                f.confidence = ConfidenceCalculator.evaluate(
                    sample_size=f.sample_count,
                    data_quality=data_quality,
                ).confidence_score

        severities = {f.severity for f in findings}
        health = "CRITICAL" if "CRITICAL" in severities else "NEEDS_ATTENTION" if findings else "HEALTHY"
        recs = self._select_recommendations(findings)
        summary = self._summary(metrics, findings)

        return AgentAnalysisOutput(
            agent=self.agent_name,
            execution_id=exec_id,
            timestamp=now,
            confidence=round(confidence, 3),
            health=health,
            summary=summary,
            metrics=metrics,
            findings=findings,
            recommendations=recs,
            charts=charts,
            tool_calls=[_tc(x) for x in tool_calls],
            llm_backed=False,
        )

    # API-compat aliases
    def run_analysis(self, db=None, **_: Any) -> AgentAnalysisOutput:
        return self.analyze(db=db)

    def query(self, message: str, db=None, history: list | None = None, **_: Any) -> AgentQueryResponse:
        return self.chat(message, db=db, history=history)

    def _live_source_block(self, exec_id: str, now: str) -> AgentAnalysisOutput | None:
        """None when historic (or the agent is live-aware); a clean NOT_ESTIMABLE
        output when Live is active and this agent's tools only know Olist/DataCo."""
        try:
            from app.services.data_source_service import data_source_service

            if not data_source_service.is_live():
                return None
        except Exception:
            return None
        reason = (
            f"The live Shopify data source doesn't have {self.agent_name} analytics yet "
            "(this agent still only reads the historic Olist/DataCo dataset) — switch back "
            "to Historic to see it, or ask for this to be added."
        )
        return AgentAnalysisOutput(
            agent=self.agent_name,
            execution_id=exec_id,
            timestamp=now,
            status="NOT_ESTIMABLE",
            health="NOT_ESTIMABLE",
            not_estimable_reason=reason,
            summary=f"No live-source data for {self.display_name} yet.",
        )

    # subclasses override these three
    def _render_metric(self, tool_name: str, data: Any) -> tuple[list[MetricCard], Any | None, int]:
        return [], data if isinstance(data, list) else None, _sample_of(data)

    def _select_recommendations(self, findings: list[Finding]) -> list[Recommendation]:
        if not findings:
            return []
        out: list[Recommendation] = []
        # Primary recommendations come straight from the live findings (real,
        # data-driven actions for exactly what was observed), highest severity
        # first.
        for f in sorted(findings, key=lambda x: -_SEV_ORDER.get(x.severity, 0))[:4]:
            out.append(
                Recommendation(
                    title=f.title,
                    detail=f.recommended_action,
                    expected_impact=f.why_it_matters,
                    priority="HIGH" if f.severity in ("HIGH", "CRITICAL") else "MEDIUM",
                )
            )
        # Playbook items are only appended when they actually relate to an
        # observed finding (keyword overlap with the finding text) — never as a
        # blanket dump of static copy.
        for r in self.recommendation_playbook:
            if len(out) >= 6:
                break
            hay = f"{r.title} {r.detail}".lower().split()
            if any(
                w in hay
                for f in findings
                for w in f"{f.title} {f.what_happened}".lower().split()
                if len(w) > 3
            ):
                prio = "HIGH" if any(f.severity in ("HIGH", "CRITICAL") for f in findings) else "MEDIUM"
                out.append(r.model_copy(update={"priority": prio if r.priority == "AUTO" else r.priority}))
        return out

    def _summary(self, metrics: list[MetricCard], findings: list[Finding]) -> str:
        head = "; ".join(f"{m.label} {m.value}" for m in metrics[:4])
        tail = (
            f" — {len(findings)} finding(s) need attention."
            if findings
            else " — all dimensions within empirical baseline."
        )
        return (head + tail).strip()

    # ── chat ───────────────────────────────────────────────────
    def chat(self, message: str, db=None, history: list | None = None) -> AgentQueryResponse:
        analysis = self.analyze(db=db)
        if (
            not self.supports_live_source
            and analysis.not_estimable_reason
            and "live Shopify data source" in analysis.not_estimable_reason
        ):
            # Don't fall through to _react_chat/_deterministic_chat — both would
            # invoke tools that only know Olist/DataCo, silently answering from
            # the historic dataset while "Live" is selected.
            return AgentQueryResponse(
                agent=self.agent_name,
                intent="not_estimable",
                answer=analysis.not_estimable_reason,
                data={"summary": analysis.summary},
                llm_backed=False,
            )
        context = {
            "summary": analysis.summary,
            "health": analysis.health,
            "metrics": [m.model_dump() for m in analysis.metrics],
            "findings": [f.model_dump() for f in analysis.findings],
            "recommendations": [r.model_dump() for r in analysis.recommendations],
            "charts": dict(analysis.charts.items()),
        }
        model = get_chat_model()
        if model is not None:
            try:
                trace: list[dict] = []
                answer = self._react_chat(model, message, history=history, analysis=analysis, trace=trace)
                if answer:
                    if trace:
                        context = {**context, "tool_trace": trace}
                    return AgentQueryResponse(
                        agent=self.agent_name, intent="react", answer=answer, data=context, llm_backed=True
                    )
            except Exception as exc:
                logger.warning("react_chat_failed", agent=self.agent_name, error=str(exc))

        return AgentQueryResponse(
            agent=self.agent_name,
            intent="deterministic",
            answer=self._deterministic_chat(message, analysis, history=history),
            data=context,
            llm_backed=False,
        )

    def _react_chat(
        self,
        model,
        message: str,
        history: list | None = None,
        analysis: AgentAnalysisOutput | None = None,
        trace: list[dict] | None = None,
    ) -> str:
        from langgraph.prebuilt import create_react_agent

        from app.agents.customer.langchain_tools import resolve_unknown_id

        register_user_message(message)
        tools = list(self.metric_tools) + list(self.lookup_tools) + [resolve_unknown_id] + _CALCULATOR
        live = ""
        if analysis is not None:
            metric_lines = "; ".join(f"{m.label}={m.value}" for m in analysis.metrics[:5])
            finding_lines = " | ".join(f"[{f.severity}] {f.title}" for f in analysis.findings[:3])
            live = (
                f"\n\nCurrent analysis snapshot for this agent (already computed from the live database — "
                f"reuse these numbers instead of re-querying when they answer the question):\n"
                f"Summary: {analysis.summary}\nMetrics: {metric_lines or 'none'}\n"
                f"Findings: {finding_lines or 'none'}"
            )
        agent = create_react_agent(
            model,
            tools,
            prompt=(
                f"{self.persona}\n\n"
                "You are a precise, professional assistant. Rules:\n"
                "1. Answer EXACTLY what was asked — nothing more. No preamble, no filler, no "
                "restating the question, no unsolicited extras. If the user asks one number, reply "
                "with that number and its unit (plus one short clause of context at most).\n"
                "2. For anything about the business data (orders, shipments, carriers, revenue, "
                "prices, discounts, customers, products, reviews, SLA), call the relevant data tool "
                "first. NEVER invent numbers, entities, or categories — every business figure must "
                "come from tool output. Cite the key figures you used.\n"
                "3. Use the `calculator` tool for ANY arithmetic on tool numbers (percentages, "
                "differences, ratios, averages). Never do mental math on data.\n"
                "4. You also have a universal entity lookup tool (`resolve_unknown_id`) for "
                "inspecting any ID from any table (orders, customers, products, sellers, reviews).\n"
                "5. The user may also ask general questions that need no database (definitions, "
                "how something works, general knowledge, small talk). Answer those directly from "
                "your own knowledge — do NOT call tools and do NOT refuse. If a question mixes "
                "general context with business data, fetch the data and fold it in.\n"
                "6. If a requested business figure genuinely doesn't exist in the data, say so in "
                "one sentence and offer the closest available figure.\n"
                '7. Use the conversation history to resolve follow-ups ("what about its status?").\n'
                "8. Tools that change state (creating a purchase order / reorder, opening an RMA) "
                "are ONE-STEP-BEFORE-CONFIRMATION: when the user asks for such an action, first "
                "fetch/preview the exact plan with the read-only tools, present it, and ask the "
                "user to reply with an explicit 'confirm'. Only pass confirm=True after the user "
                "has actually replied with that confirmation. If the tool returns "
                "CONFIRMATION_REQUIRED, do NOT retry — tell the user what you need."
                f"{live}"
            ),
        )
        messages: list[Any] = []
        for turn in (history or [])[-8:]:
            role = turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", "user")
            text = turn.get("text") if isinstance(turn, dict) else getattr(turn, "text", "")
            if not text:
                continue
            messages.append(HumanMessage(content=text) if role == "user" else AIMessage(content=text))
        messages.append(HumanMessage(content=message))
        result = self._invoke_react_with_retry(agent, messages, model)
        msgs = result.get("messages", [])
        executed = _collect_tool_trace(msgs)
        if trace is not None:
            trace.extend(executed)
        logger.info(
            "react_completed",
            agent=self.agent_name,
            tool_calls=len(executed),
            tools_used=[t.get("tool") for t in executed],
        )
        for m in reversed(msgs):
            if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
                return m.content if isinstance(m.content, str) else str(m.content)
        return ""

    @staticmethod
    def _invoke_react_with_retry(agent, messages: list, model):
        """Invoke the ReAct graph once, retrying a metered-provider rate limit
        (429) after the provider-suggested wait. Anything else propagates."""
        try:
            return agent.invoke({"messages": messages}, config={"recursion_limit": 12})
        except Exception as exc:
            wait = _rate_limit_wait(exc)
            if wait is None:
                raise
            logger.warning(
                "react_rate_limited_retry", agent=getattr(agent, "name", "agent"), wait_s=round(wait, 1)
            )
            time.sleep(wait)
            return agent.invoke({"messages": messages}, config={"recursion_limit": 12})

    def _deterministic_chat(
        self, message: str, analysis: AgentAnalysisOutput, history: list | None = None
    ) -> str:
        """
        No-LLM path: still runs the LangChain tools. If the question (or, failing
        that, a recent turn in `history`) references an entity or order id,
        answers that specific entity first using domain lookup tools or the
        cross-table entity resolver. Otherwise picks the tool(s) whose
        name/description best matches the question.
        """
        low = (message or "").lower().strip()
        lines = [f"**{self.display_name}** — {analysis.summary}", ""]

        if not low or any(k in low for k in ("hello", "hi ", "hey ", "help", "what can you", "capabilit")):
            lines.append(
                "I can pull: " + ", ".join(t.name.replace("_", " ") for t in self.metric_tools) + "."
            )
            for m in analysis.metrics:
                lines.append(f"- {m.label}: {m.value}" + (f" — {m.description}" if m.description else ""))
            return "\n".join(lines)

        rendered = False

        # entity-specific: an ID in this message or a recent follow-up turn
        from app.agents._shared import extract_entity_id
        from app.agents.entity_resolver import entity_resolver

        cand_id, _ = extract_entity_id(message)
        if not cand_id and history:
            for turn in reversed(history):
                txt = turn.get("text") if isinstance(turn, dict) else getattr(turn, "text", "")
                cand_id, _ = extract_entity_id(txt or "")
                if cand_id:
                    break

        if cand_id:
            resolved = entity_resolver.resolve_entity(cand_id)
            if resolved.get("status") != "FOUND":
                # Name the id and say plainly it does not exist — the answer
                # must still reference what the user asked about.
                lines.append(
                    resolved.get("summary", "") or f"ID '{cand_id}' was not found in any database table."
                )
                rendered = True
            if resolved.get("status") == "FOUND":
                if resolved.get("entity_type") == "order" and self.lookup_tools:
                    for t in self.lookup_tools:
                        try:
                            out = _as_obj(t.invoke({"order_id": cand_id}))
                            if isinstance(out, dict) and out.get("status") == "NOT_FOUND":
                                continue
                            lines.append(f"**{_titleize(t.name)}** (order #{cand_id}):")
                            lines.extend(_render_rows(out))
                            rendered = True
                            break
                        except Exception:
                            continue
                if not rendered:
                    lines.append(resolved.get("summary", ""))
                    rendered = True

        # score tools by keyword overlap with the question
        if not rendered:
            scored: list[tuple[int, BaseTool]] = []
            toks = {w for w in _re_words(low) if len(w) > 2}
            for t in list(self.metric_tools) + list(self.lookup_tools):
                hay = f"{t.name} {t.description}".lower()
                score = sum(1 for w in toks if w in hay)
                if score:
                    scored.append((score, t))
            scored.sort(key=lambda s: -s[0])

            for _, t in scored[:2]:
                out = None
                # Try empty arguments first (for zero-arg metric tools)
                try:
                    out = _as_obj(t.invoke({}))
                except Exception:
                    pass

                # If tool expects metric argument (e.g. analytics tools), select best metric
                if out is None and "metric" in (t.description or "").lower():
                    desc = (t.description or "").lower()
                    candidates = re.findall(r'["\']([a-zA-Z0-9_]+)["\']', desc)
                    best_m, best_score = None, -1
                    for cand in candidates:
                        cand_words = cand.replace("_", " ").split()
                        c_score = sum(2 for w in cand_words if w in low)
                        if cand.replace("_", " ") in low:
                            c_score += 5
                        if c_score > best_score:
                            best_score = c_score
                            best_m = cand
                    if best_m:
                        try:
                            out = _as_obj(t.invoke({"metric": best_m}))
                        except Exception:
                            pass

                # If tool expects query parameter
                if out is None:
                    for arg_name in ("query", "product_id_or_keyword", "keyword"):
                        try:
                            out = _as_obj(t.invoke({arg_name: low}))
                            break
                        except Exception:
                            pass

                if out is not None:
                    lines.append(f"**{_titleize(t.name)}**:")
                    lines.extend(_render_rows(out))
                    rendered = True
                    break

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
def _rate_limit_wait(exc: Exception) -> float | None:
    """Seconds to wait before retrying a provider rate limit, or None when the
    exception is not a rate limit. Honours the provider's suggested delay."""
    text = str(exc)
    name = type(exc).__name__
    if name not in ("RateLimitError", "APIStatusError") and "rate_limit" not in text and "429" not in text:
        return None
    delay = 6.0
    match = re.search(r"try again in ([0-9.]+)s", text, re.IGNORECASE)
    if match:
        delay = max(delay, float(match.group(1)))
    return min(delay + 0.5, 15.0)


def _collect_tool_trace(msgs: list) -> list[dict]:
    """Pair each AIMessage.tool_calls entry with its ToolMessage output, in
    execution order — the audit trail proving which tools answered a query."""
    trace: list[dict] = []
    for m in msgs:
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                trace.append({"tool": tc.get("name"), "input": tc.get("args")})
        elif isinstance(m, ToolMessage) and trace:
            for entry in reversed(trace):
                if "output" not in entry:
                    entry["output"] = str(m.content)[:1500]
                    break
    return trace


def _as_obj(raw: Any) -> Any:
    if isinstance(raw, dict | list):
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
            if isinstance(data.get(k), int | float):
                return int(data[k])
    if isinstance(data, list):
        return len(data)
    return 0


def _re_words(s: str) -> list[str]:
    import re

    return re.findall(r"[a-z][a-z\-]+", s)


_ACRONYMS = {"rfm", "sla", "rop", "eoq", "roi", "csat", "ltv", "cac", "sku"}

_SEV_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _titleize(name: str) -> str:
    words = name.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in words)


def _field_label(k: str) -> str:
    return _titleize(k.strip())


def _field_value(k: str, v: Any) -> str:
    kl = k.lower()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, int | float):
        if "pct" in kl or "percent" in kl:
            return f"{v}%"
        if any(
            t in kl
            for t in (
                "revenue",
                "profit",
                "price",
                "cost",
                "value",
                "total",
                "freight",
                "discount",
                "margin_amount",
            )
        ):
            return money(v)
        return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}"
    return str(v) if v is not None else "—"


def _render_row(row: dict) -> str:
    parts = [
        f"{_field_label(k)}: {_field_value(k, v)}"
        for k, v in row.items()
        if v is not None and not isinstance(v, dict | list)
    ]
    return "- " + " · ".join(parts)


def _render_rows(out: Any) -> list[str]:
    if isinstance(out, dict):
        # a list-of-rows under some key?
        for v in out.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [_render_row(row) for row in v[:8]]
        return [
            f"- {_field_label(k)}: {_field_value(k, v)}"
            for k, v in out.items()
            if not isinstance(v, dict | list)
        ][:12]
    if isinstance(out, list):
        return [_render_row(row) if isinstance(row, dict) else f"- {row}" for row in out[:8]]
    return [f"- {out}"]
