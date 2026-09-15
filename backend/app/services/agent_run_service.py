"""
Persists an agent's analysis output as `agent_runs` + `agent_findings`, emits a
platform event, and (optionally) proposes automation actions from the findings.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.database.session import SessionLocal
from app.models.operations import AgentFinding, AgentRun
from app.models.security import _uuid
from app.services.event_bus import publish_event

logger = get_logger("agent_run")


def _finding_dicts(output: Any) -> list[dict]:
    out = []
    for f in getattr(output, "findings", []) or []:
        d = f.model_dump() if hasattr(f, "model_dump") else dict(f)
        out.append(d)
    return out


def record_analysis(
    output: Any,
    *,
    agent: str,
    trigger: str = "api",
    latency_ms: float | None = None,
    propose_actions: bool = True,
) -> str | None:
    """Write the run + findings. Returns the run id (or None on failure — never raises)."""
    try:
        db = SessionLocal()
    except Exception:
        return None
    try:
        findings = _finding_dicts(output)
        run = AgentRun(
            id=_uuid(),
            agent=agent,
            trigger=trigger,
            execution_id=getattr(output, "execution_id", None),
            status=getattr(output, "status", "SUCCESS"),
            health=getattr(output, "health", None)
            or getattr(getattr(output, "health", None), "status", None),
            confidence=getattr(output, "confidence", None),
            summary=getattr(output, "summary", None),
            latency_ms=latency_ms,
            llm_backed=bool(getattr(output, "llm_backed", False)),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        db.add(run)
        db.flush()
        for f in findings:
            db.add(
                AgentFinding(
                    id=_uuid(),
                    run_id=run.id,
                    agent=agent,
                    category=f.get("category", "GENERAL"),
                    severity=f.get("severity", "MEDIUM"),
                    title=(f.get("title") or f.get("what_happened") or "finding")[:300],
                    what_happened=f.get("what_happened"),
                    why_it_matters=f.get("why_it_matters"),
                    recommended_action=f.get("recommended_action"),
                    evidence=f.get("evidence"),
                    confidence=f.get("confidence"),
                    data_status=f.get("data_status", "CALCULATED"),
                )
            )
        db.commit()
        publish_event(
            "agent_runs",
            {
                "type": "run_completed",
                "agent": agent,
                "run_id": run.id,
                "health": run.health,
                "findings": len(findings),
                "llm_backed": run.llm_backed,
            },
        )

        if propose_actions and findings:
            try:
                from app.automation import propose_for_run

                propose_for_run(db, agent=agent, findings=findings)
            except Exception as exc:
                logger.warning("propose_actions_failed", agent=agent, error=str(exc))

        return run.id
    except Exception as exc:
        logger.warning("record_analysis_failed", agent=agent, error=str(exc))
        with contextlib.suppress(Exception):
            db.rollback()
        return None
    finally:
        db.close()
