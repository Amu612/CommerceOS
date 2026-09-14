"""Domain agent routers: logistics, pricing, marketing (orders/inventory/customer stay on legacy paths until M7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.common_schemas import AgentAnalysisOutput, AgentQueryResponse
from app.agents.logistics import logistics_agent
from app.agents.marketing import marketing_agent
from app.agents.pricing import pricing_agent
from app.database.session import get_db


class _Query(BaseModel):
    query: str | None = None
    message: str | None = None
    history: list[dict] | None = None


def _make_router(prefix: str, agent, tag: str) -> APIRouter:
    r = APIRouter(prefix=prefix, tags=[tag])

    @r.get("/analyze", response_model=AgentAnalysisOutput)
    @r.post("/analyze", response_model=AgentAnalysisOutput)
    def analyze(db: Session = Depends(get_db)):
        import time

        from app.services.agent_run_service import record_analysis

        t0 = time.perf_counter()
        out = agent.run_analysis(db=db)
        try:
            record_analysis(out, agent=agent.agent_name, trigger="api",
                            latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        except Exception:  # noqa: BLE001 - persistence must never fail the request
            pass
        return out

    @r.get("/runs", tags=[tag])
    def runs(limit: int = 20, db: Session = Depends(get_db)):
        from sqlalchemy import desc

        from app.models.operations import AgentRun

        rows = db.query(AgentRun).filter(AgentRun.agent == agent.agent_name).order_by(desc(AgentRun.started_at)).limit(limit).all()
        return {"items": [
            {"id": x.id, "health": x.health, "confidence": x.confidence, "summary": x.summary,
             "findings": len(x.findings), "llm_backed": x.llm_backed,
             "started_at": x.started_at.isoformat() if x.started_at else None}
            for x in rows
        ]}

    @r.post("/query", response_model=AgentQueryResponse)
    def query(payload: _Query, db: Session = Depends(get_db)):
        msg = (payload.query or payload.message or "").strip()
        if not msg:
            return AgentQueryResponse(agent=agent.agent_name, answer="Please enter a question.", success=False)
        return agent.query(message=msg, db=db, history=payload.history)

    return r


logistics_router = _make_router("/api/v1/agents/logistics", logistics_agent, "Logistics Agent")
pricing_router = _make_router("/api/v1/agents/pricing", pricing_agent, "Pricing Agent")
marketing_router = _make_router("/api/v1/agents/marketing", marketing_agent, "Marketing Agent")


class _RouteRequest(BaseModel):
    order_id: str
    seller_id: str | None = None


@logistics_router.get("/route")
@logistics_router.post("/route")
def get_order_route(
    order_id: str | None = None,
    seller_id: str | None = None,
    payload: _RouteRequest | None = None,
    db: Session = Depends(get_db),
):
    from app.agents.logistics.data_layer import LogisticsData

    target_order_id = (payload.order_id if payload else None) or order_id or ""
    target_seller_id = (payload.seller_id if payload else None) or seller_id
    return LogisticsData.order_route_lookup(db=db, order_id=target_order_id, seller_id=target_seller_id)


# Pricing-only: the Apify competitor-price feed. Additive — works the same
# regardless of which order data source (historic/live) is active.
@pricing_router.get("/competitor-feed")
def competitor_feed_status():
    from app.core.settings import settings

    return {"enabled": settings.PRICING_COMPETITOR_FEED_ENABLED, "configured": settings.apify_configured}


@pricing_router.post("/competitor-feed/sync")
def competitor_feed_sync(category: str | None = None):
    from app.services.apify_service import sync_competitor_prices

    return sync_competitor_prices(category=category)


__all__ = ["logistics_router", "pricing_router", "marketing_router"]
