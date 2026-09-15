import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.orders import OrdersAgentOutput, OrdersQueryResponse, orders_agent
from app.database.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["Orders Agent"])
orders_router = router


class AnalysisRequest(BaseModel):
    generate_notifications: bool = True
    execution_id: str | None = None
    snapshot_id: str | None = None


class QueryRequest(BaseModel):
    message: str | None = None
    query: str | None = None
    history: list | None = None


@router.post("/analyze", response_model=OrdersAgentOutput)
@router.get("/analyze", response_model=OrdersAgentOutput)
def analyze_orders(
    request: Request,
    payload: AnalysisRequest | None = None,
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
    action: str | None = "start"  # "start", "pause", "resume", "stop", "reset", "step", "complete"
    speed: int | None = None
    step: int | None = 50
    clear_db: bool | None = True


from app.services.replay_engine import replay_engine  # noqa: E402

# A dedicated router, not more routes on `orders_router` — ingestion/replay
# control mutates the shared dataset every single agent reads (and `reset`
# can wipe it outright), so it's a platform-wide administrative capability,
# not something scoped to "the Orders agent" just because its URL happens to
# start with /api/orders for backwards compatibility. Gated SUPER_ADMIN-only
# in main.py (see `ingestion_dependency`) — separately from, and stricter
# than, `agent_dependency("orders")`.
ingestion_router = APIRouter(prefix="/api/orders", tags=["Ingestion"])


@ingestion_router.get("/ingestion/status")
def get_ingestion_status():
    """Returns the current data ingestion / streaming status."""
    return replay_engine.get_status()


@ingestion_router.post("/ingestion/control")
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

    # start/pause/resume/stop are cheap flag flips — fine directly on the
    # event loop. step/complete/reset do real synchronous DB + pandas work
    # (complete_now() alone can ingest thousands of rows in one call) and
    # this route is `async def` (needed for `await replay_engine.start()`),
    # so FastAPI does NOT offload it to a worker thread the way a plain `def`
    # route gets — calling them directly here would freeze every other
    # in-flight request on the server for the whole duration.
    if action == "start":
        msg = await replay_engine.start()
    elif action == "pause":
        msg = replay_engine.pause()
    elif action == "resume":
        msg = replay_engine.resume()
    elif action == "stop":
        msg = replay_engine.stop()
    elif action == "reset":
        clear_db = payload.clear_db if payload.clear_db is not None else True
        msg = await asyncio.to_thread(replay_engine.reset, clear_db=clear_db)
    elif action == "step":
        count = payload.step or 50
        ingested = await asyncio.to_thread(replay_engine.step, count=count)
        msg = f"Stepped {ingested} records into the system."
    elif action == "complete":
        msg = await asyncio.to_thread(replay_engine.complete_now)
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
