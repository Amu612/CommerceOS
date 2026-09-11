import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database.session import get_db
from app.agents.orders import orders_agent, OrdersAgentOutput, OrdersQueryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["Orders Agent"])
orders_router = router


class AnalysisRequest(BaseModel):
    generate_notifications: bool = True
    execution_id: Optional[str] = None
    snapshot_id: Optional[str] = None


class QueryRequest(BaseModel):
    message: Optional[str] = None
    query: Optional[str] = None
    history: Optional[list] = None


@router.post("/analyze", response_model=OrdersAgentOutput)
@router.get("/analyze", response_model=OrdersAgentOutput)
def analyze_orders(
    request: Request,
    payload: Optional[AnalysisRequest] = None,
    db: Session = Depends(get_db),
):
    """
    Executes real Orders Agent empirical analysis across Olist and DataCo transaction records.
    Called directly by OrdersAgentDashboard.tsx loadAnalysis().
    """
    gen_notifs = payload.generate_notifications if payload else True
    exec_id = payload.execution_id if payload else None
    snap_id = payload.snapshot_id if payload else None

    result = orders_agent.run_analysis(
        db=db,
        execution_id=exec_id,
        snapshot_id=snap_id,
        generate_notifications=gen_notifs,
    )
    return result


@router.post("/query", response_model=OrdersQueryResponse)
def query_orders(
    payload: QueryRequest,
    db: Session = Depends(get_db),
):
    """
    Interactive Orders Agent operation endpoint.
    Handles order status lookups, shipment tracking, return eligibility, and RMA creation.
    Called directly by OrdersAgentDashboard.tsx runQuery().
    """
    user_msg = payload.message or payload.query or ""
    if not user_msg.strip():
        return OrdersQueryResponse(
            intent="empty",
            order_id=None,
            result="Please provide a message or order inquiry.",
            success=False,
        )

    res = orders_agent.query(message=user_msg.strip(), db=db, history=payload.history)
    return res


@router.get("/latest", response_model=OrdersAgentOutput)
def get_latest_orders_analysis(db: Session = Depends(get_db)):
    """Returns the latest calculated orders analysis."""
    return orders_agent.get_latest_analysis(db=db)


@router.get("/health")
def get_orders_health(db: Session = Depends(get_db)):
    """Quick operational health check for Orders Agent."""
    output = orders_agent.get_latest_analysis(db=db)
    return {
        "status": output.health.status,
        "summary": output.health.summary_message,
        "active_issues": output.health.active_issues_count,
        "confidence": output.confidence,
    }


@router.post("/reset")
def reset_orders_agent(db: Session = Depends(get_db)):
    """Resets orders agent cache and resolves all active orders notifications."""
    orders_agent.reset(db=db)
    return {"status": "SUCCESS", "message": "Orders agent reset successfully."}


class IngestionControlRequest(BaseModel):
    action: Optional[str] = "start"  # "start", "pause", "resume", "stop", "reset", "step"
    speed: Optional[int] = None
    step: Optional[int] = 50
    clear_db: Optional[bool] = True


from app.services.replay_engine import replay_engine


@router.get("/ingestion/status")
def get_ingestion_status():
    """Returns the current data ingestion / streaming status."""
    return replay_engine.get_status()


@router.post("/ingestion/control")
async def control_ingestion(payload: IngestionControlRequest):
    """
    Controls the flow of data ingestion into the system:
    actions: start, pause, resume, stop, reset, step.
    Optionally set ingestion speed (records/second).
    """
    if payload.speed is not None and payload.speed > 0:
        replay_engine.set_speed(payload.speed)

    action = (payload.action or "status").lower().strip()
    msg = ""

    if action == "start":
        msg = await replay_engine.start()
    elif action == "pause":
        msg = replay_engine.pause()
    elif action == "resume":
        msg = replay_engine.resume()
    elif action == "stop":
        msg = replay_engine.stop()
    elif action == "reset":
        msg = replay_engine.reset(clear_db=payload.clear_db if payload.clear_db is not None else True)
    elif action == "step":
        count = payload.step or 50
        ingested = replay_engine.step(count=count)
        msg = f"Stepped {ingested} records into the system."
    else:
        msg = f"Unknown action '{action}'."

    status = replay_engine.get_status()
    return {
        "status": "success",
        "action": action,
        "message": msg,
        "ingestion": status,
    }


# Create simulation router for Nexus-compatible paths (/api/simulation and /dashboard/simulation)
simulation_router = APIRouter(prefix="/api/simulation", tags=["Simulation"])
nexus_sim_router = APIRouter(prefix="/dashboard/simulation", tags=["Nexus Simulation"])


@simulation_router.get("/status")
@nexus_sim_router.get("/status")
def get_simulation_status():
    return replay_engine.get_status()


@simulation_router.post("/control")
@nexus_sim_router.post("/control")
async def control_simulation(payload: IngestionControlRequest):
    return await control_ingestion(payload)

