"""Orchestrator API — one cross-domain sweep of all agents."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.orchestrator import OrchestrationResult, orchestrator

router = APIRouter(prefix="/api/v1/orchestrator", tags=["Orchestrator"])


def _persist(result: OrchestrationResult) -> None:
    try:
        from app.database.session import SessionLocal
        from app.models.operations import OrchestrationDecision
        from app.models.security import _uuid

        db = SessionLocal()
        try:
            decision = OrchestrationDecision(
                id=_uuid(), execution_id=result.execution_id,
                overall_health=result.overall_health, overall_confidence=result.overall_confidence,
                summary=result.summary,
                domains=[d.model_dump() for d in result.domains],
                systemic_findings=[s.model_dump() for s in result.systemic_findings],
                conflicts=[c.model_dump() for c in result.conflicts],
                priority_actions=result.priority_actions, kpis=result.kpis, llm_backed=result.llm_backed,
            )
            db.add(decision)
            db.commit()

            # Propose automation actions from every domain's findings.
            try:
                from app.automation import propose_for_run

                for d in result.domains:
                    findings = [
                        {"category": f.category, "severity": f.severity, "title": f.title,
                         "recommended_action": f.recommended_action, "confidence": f.confidence}
                        for f in d.findings
                    ]
                    if findings:
                        propose_for_run(db, agent=d.agent, findings=findings, decision_id=decision.id)
            except Exception:  # noqa: BLE001
                pass
            from app.services.event_bus import publish_event

            publish_event("orchestrator", {"type": "sweep_completed", "execution_id": result.execution_id,
                                           "health": result.overall_health, "systemic": len(result.systemic_findings)})
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass


@router.post("/run", response_model=OrchestrationResult)
def run(db: Session = Depends(get_db)):
    """Execute a fresh cross-domain sweep. Expensive (runs every agent + LLM) — call
    on explicit user action only. Dashboards should poll ``/latest`` instead."""
    result = orchestrator.run(db=db)
    _persist(result)
    return result


@router.get("/latest", tags=["Orchestrator"])
def latest(db: Session = Depends(get_db)):
    """Return the most recent persisted sweep without re-running anything.

    Falls back to a single live sweep only when nothing has been persisted yet,
    so the dashboard has something to show on a cold database.
    """
    from sqlalchemy import desc

    from app.models.operations import OrchestrationDecision

    row = (
        db.query(OrchestrationDecision)
        .order_by(desc(OrchestrationDecision.created_at))
        .first()
    )
    if row is None:
        result = orchestrator.run(db=db)
        _persist(result)
        return result.model_dump()

    return {
        "execution_id": row.execution_id,
        "timestamp": row.created_at.isoformat() if row.created_at else None,
        "overall_health": row.overall_health,
        "overall_confidence": row.overall_confidence,
        "summary": row.summary,
        "domains": row.domains or [],
        "systemic_findings": row.systemic_findings or [],
        "conflicts": row.conflicts or [],
        "priority_actions": row.priority_actions or [],
        "kpis": row.kpis or {},
        "llm_backed": bool(row.llm_backed),
        "stale": True,
    }


@router.get("/run", response_model=OrchestrationResult, deprecated=True)
def run_get(db: Session = Depends(get_db)):
    """Deprecated GET alias for POST /run — kept for older clients."""
    result = orchestrator.run(db=db)
    _persist(result)
    return result


@router.get("/decisions", tags=["Orchestrator"])
def decisions(limit: int = 20, db: Session = Depends(get_db)):
    from sqlalchemy import desc

    from app.models.operations import OrchestrationDecision

    rows = db.query(OrchestrationDecision).order_by(desc(OrchestrationDecision.created_at)).limit(limit).all()
    return {"items": [
        {"id": r.id, "execution_id": r.execution_id, "overall_health": r.overall_health,
         "summary": r.summary, "systemic_findings": r.systemic_findings, "conflicts": r.conflicts,
         "priority_actions": r.priority_actions,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


@router.get("/overview", response_model=OrchestrationResult)
def overview(db: Session = Depends(get_db)):
    return orchestrator.run(db=db)
