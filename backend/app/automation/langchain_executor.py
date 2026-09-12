"""
LangChain-backed autonomous execution for approved/auto automation actions.

Design (mirrors `app.agents.framework._react_chat`'s "LLM when available,
deterministic otherwise" pattern used everywhere else in this codebase):

  * Every registered handler in `app.automation.actions.HANDLERS` is wrapped
    as a LangChain `StructuredTool`. This is what "automation uses LangChain"
    means concretely — the executable unit of automation is a LangChain tool,
    not a bare function call.
  * The handler itself still performs the actual state change (writes the
    notification / stock movement / audit row) — an LLM is never allowed to
    fabricate that a task happened. What LangChain adds is the *orchestration*:
    a `create_react_agent` is hand the single tool that matches the approved
    `action_type` and told to execute it now, with the recorded finding as
    context, so it runs end-to-end "on its own" and narrates why in the audit
    trail — without a human choosing among tools or writing the call.
  * When no chat model is configured (`get_chat_model()` returns None — no
    API key, offline, provider unreachable), the tool is invoked directly.
    This is the same LLM-less fallback every agent in this project already
    uses, so automation keeps executing (and succeeding) with zero external
    dependencies — it just skips the narration step.

Either path always returns the handler's real result dict; only an extra
`automation_backend` / `automation_summary` key is layered on top.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from app.core.logging import get_logger

logger = get_logger("automation.langchain")


def _make_tool(action_type: str, fn: Callable[[dict, Session], dict], box: dict) -> StructuredTool:
    """Wraps one automation handler as a LangChain tool that records its
    return value into `box` — the agent sees a short confirmation string
    (LLMs handle free text far more reliably than being asked to echo a
    nested dict back verbatim), while the real structured result is kept
    for the executor/UI."""

    def _run(**kwargs: Any) -> str:  # noqa: ARG001 - payload is closed over, not LLM-supplied
        try:
            result = fn(box["payload"], box["db"])
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed; see call site
            box["error"] = exc
            return f"Tool call failed: {exc}"
        box["result"] = result
        effect = result.get("effect") or ("notified" if result.get("notification_id") else "recorded")
        return f"Executed {action_type}: {effect}."

    return StructuredTool.from_function(
        func=_run,
        name=action_type.lower(),
        description=f"Executes the approved automation action '{action_type}'. Takes no arguments — call it with no input to run it now.",
    )


def execute_via_langchain(
    action_type: str, payload: dict, db: Session, handler_fn: Callable[[dict, Session], dict]
) -> dict:
    """
    Runs `handler_fn(payload, db)` — via a LangChain react agent that
    autonomously calls it as a tool when an LLM is configured, or directly
    otherwise. Always returns the handler's result dict (plus an
    `automation_backend` bookkeeping key); never swallows a genuine handler
    failure — it propagates exactly like a direct call would, so the
    executor's own retry/FAILED handling still applies.
    """
    box: dict[str, Any] = {"payload": payload, "db": db, "result": None, "error": None}

    model = None
    try:
        from app.services.llm.chat_model import get_chat_model

        model = get_chat_model()
    except Exception:  # noqa: BLE001 - LLM layer must never block automation
        model = None

    if model is None:
        result = handler_fn(payload, db)
        result["automation_backend"] = "deterministic"
        return result

    try:
        from langgraph.prebuilt import create_react_agent
        from langchain_core.messages import HumanMessage

        tool = _make_tool(action_type, handler_fn, box)
        agent = create_react_agent(
            model,
            [tool],
            prompt=(
                "You are the CommerceOS automation runner. Exactly one action has already "
                "been approved for execution — you MUST call the single tool you were given "
                "(with no arguments) to carry it out now, then reply with a one-sentence "
                "confirmation of what was done. Never ask questions, never skip the tool call."
            ),
        )
        title = payload.get("title") or action_type
        detail = payload.get("detail") or ""
        agent.invoke(
            {"messages": [HumanMessage(content=f"Execute the approved action now: {title}. {detail}")]},
            config={"recursion_limit": 4},
        )
    except Exception as exc:  # noqa: BLE001 - LLM/agent-plumbing failure; the tool itself never ran
        logger.warning("langchain_automation_agent_failed", action=action_type, error=str(exc))

    if box["error"] is not None:
        # The tool DID run and the handler itself raised (e.g. a real DB
        # error) — that must propagate so the caller's FAILED/retry handling
        # applies, exactly like a non-LangChain call would. Retrying it here
        # inline would duplicate the handler's side effect instead of going
        # through the executor's single retry path.
        raise box["error"]

    if box["result"] is None:
        # The agent didn't call the tool (unlikely given the prompt, but the
        # task must execute regardless) — run it directly so the action never
        # sits un-executed just because the LLM's tool-call step misfired.
        box["result"] = handler_fn(payload, db)
        box["result"]["automation_backend"] = "deterministic_fallback"
    else:
        box["result"]["automation_backend"] = "langchain"

    return box["result"]
