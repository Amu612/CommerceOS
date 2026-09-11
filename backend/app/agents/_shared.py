"""
Shared helpers for the domain agents. Keeps every agent source-aware to the
replay simulated clock and consistent about provenance tagging.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dataco import DataCoOrder
from app.models.olist import Order


def tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def simulated_clock(db: Optional[Session] = None) -> datetime:
    """
    Current simulated time T. Only records with timestamp <= T are 'observed'.

    When the replay engine is idle (nothing streamed yet), fall back to the latest
    record timestamp in the database so a fresh dashboard load shows the full
    historical picture rather than an empty view pinned to the dataset start.
    """
    try:
        from app.services.replay_engine import replay_engine
        from app.services.state_service import state_service

        streamed = getattr(replay_engine, "events_processed", 0) or 0
        if streamed > 0 and replay_engine.current_simulated_date:
            return tz(replay_engine.current_simulated_date)
        if getattr(state_service, "sim_current_date", None) and getattr(state_service, "total_orders", 0):
            return tz(state_service.sim_current_date)
    except Exception:
        pass

    own = None
    if db is None:
        from app.database.session import SessionLocal

        own = db = SessionLocal()
    try:
        candidates = [
            d
            for d in (
                db.query(func.max(Order.order_purchase_timestamp)).scalar(),
                db.query(func.max(DataCoOrder.order_date)).scalar(),
            )
            if d is not None
        ]
        if candidates:
            return tz(max(candidates))
    except Exception:
        pass
    finally:
        if own is not None:
            own.close()
    return datetime.now(timezone.utc)


def severity_from_fraction(fraction: float) -> str:
    if fraction >= 0.25:
        return "CRITICAL"
    if fraction >= 0.12:
        return "HIGH"
    if fraction >= 0.04:
        return "MEDIUM"
    return "LOW"


def pct(n: float, d: float, digits: int = 2) -> float:
    return round((n / d) * 100.0, digits) if d else 0.0


def money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def deterministic_answer(message: str, out: Any, agent_label: str, topic_map: dict) -> str:
    """
    Shared no-LLM answer builder for the domain agents.

    - "help"/"what can you do"/greeting  -> capability + headline
    - a topic keyword hit                -> that topic's detail rows
    - "why" / "explain"                  -> findings with rationale
    - "what should I do" / "recommend"   -> recommendations
    - anything else                      -> headline + top findings + key metrics
    """
    low = (message or "").lower().strip()
    lines: list[str] = [f"**{agent_label}** — {out.summary}", ""]

    if not low or any(k in low for k in ("hello", "hi ", "hey", "help", "what can you", "capabilit", "what do you")):
        lines.append(f"I analyse: {', '.join(topic_map.keys())}. Ask about any of those, or ask 'what should I do?'.")
        lines.append("")
        for m in out.metrics:
            lines.append(f"- {m.label}: {m.value}" + (f" — {m.description}" if getattr(m, 'description', None) else ""))
        return "\n".join(lines)

    for keys, render in topic_map.values():
        if any(k in low for k in keys):
            rows = render(out)
            lines.extend(rows if rows else ["- No detail available in the current data slice."])
            lines += ["", _hint()]
            return "\n".join(lines)

    if any(k in low for k in ("why", "explain", "reason", "cause", "matter")):
        for f in out.findings[:4]:
            lines.append(f"- **[{f.severity}] {f.title}** — {f.what_happened} {f.why_it_matters}")
        if not out.findings:
            lines.append("- No adverse findings — every dimension is within its empirical baseline.")
        lines += ["", _hint()]
        return "\n".join(lines)

    if any(k in low for k in ("recommend", "should i", "action", "do next", "fix", "improve", "advice")):
        for r in out.recommendations:
            lines.append(f"- **{r.title}** ({r.priority}) — {r.detail}" + (f" Impact: {r.expected_impact}" if r.expected_impact else ""))
        lines += ["", _hint()]
        return "\n".join(lines)

    # default: headline + findings + metrics
    for f in out.findings[:3]:
        lines.append(f"- **[{f.severity}] {f.title}** — {f.recommended_action}")
    if not out.findings:
        for m in out.metrics:
            lines.append(f"- {m.label}: {m.value}")
    lines += ["", _hint()]
    return "\n".join(lines)


def _hint() -> str:
    return "*Deterministic answer. Set `LLM_PROVIDER=openai` (or point `OPENAI_API_BASE` at any OpenAI-compatible endpoint) + a key for full conversational answers.*"
