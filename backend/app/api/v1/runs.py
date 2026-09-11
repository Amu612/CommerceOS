"""Agent-run history + findings."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.v1.deps import get_current_user
from app.database.session import get_read_db
from app.models.operations import AgentFinding, AgentRun
from app.models.security import User

router = APIRouter(prefix="/api/v1/runs", tags=["Agent Runs"])


@router.get("")
def list_runs(
    agent: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    q = db.query(AgentRun)
    if agent:
        q = q.filter(AgentRun.agent == agent)
    rows = q.order_by(desc(AgentRun.started_at)).limit(limit).all()
    return {
        "items": [
            {
                "id": r.id, "agent": r.agent, "trigger": r.trigger, "status": r.status,
                "health": r.health, "confidence": r.confidence, "summary": r.summary,
                "latency_ms": r.latency_ms, "llm_backed": r.llm_backed,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "findings": len(r.findings),
            }
            for r in rows
        ]
    }


@router.get("/{run_id}")
def get_run(run_id: str, _: User = Depends(get_current_user), db: Session = Depends(get_read_db)):
    r = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not r:
        from app.exceptions.base import NotFoundException

        raise NotFoundException("Run not found.")
    return {
        "id": r.id, "agent": r.agent, "health": r.health, "confidence": r.confidence,
        "summary": r.summary, "started_at": r.started_at.isoformat() if r.started_at else None,
        "findings": [
            {
                "category": f.category, "severity": f.severity, "title": f.title,
                "what_happened": f.what_happened, "why_it_matters": f.why_it_matters,
                "recommended_action": f.recommended_action, "evidence": f.evidence,
                "confidence": f.confidence, "data_status": f.data_status,
            }
            for f in r.findings
        ],
    }
