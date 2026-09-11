"""
API routes for Smart Inventory Watchdog Agent.
Exposes:
- POST /api/v1/agents/inventory/monitor
- GET  /api/v1/agents/inventory/monitor
- POST /api/v1/agents/inventory/query
- POST /api/v1/agents/inventory/reorder
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.agents.inventory import inventory_agent, InventoryAgentResponse
from app.agents.inventory.schemas import (
    InventoryQueryRequest,
    InventoryReorderRequest,
    InventoryAction,
)
from app.agents.inventory.tools import InventoryTools

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents/inventory", tags=["Inventory Agent"])
inventory_router = router

# Also alias to /api/inventory for convenience
alias_router = APIRouter(prefix="/api/inventory", tags=["Inventory Agent"])


@router.post("/monitor", response_model=InventoryAgentResponse)
@router.get("/monitor", response_model=InventoryAgentResponse)
@alias_router.post("/monitor", response_model=InventoryAgentResponse)
@alias_router.get("/monitor", response_model=InventoryAgentResponse)
def monitor_inventory(
    request: Request,
    threshold: Optional[int] = 50,
    db: Session = Depends(get_db),
):
    """
    Triggers an inventory watchdog monitoring cycle.
    Returns dynamic metrics, low stock products, recommendations, alerts, and tool execution history.
    """
    return inventory_agent.run_monitor(db=db, threshold=threshold)


@router.post("/query", response_model=InventoryAgentResponse)
@alias_router.post("/query", response_model=InventoryAgentResponse)
def query_inventory(
    payload: InventoryQueryRequest,
    db: Session = Depends(get_db),
):
    """
    Interactive inquiry endpoint for the Inventory Agent.
    Supports stock lookups, sales trends, reorder calculations, and chat.
    """
    msg = payload.query or payload.message or ""
    if not msg.strip():
        return inventory_agent.run_monitor(db=db, threshold=payload.threshold)

    return inventory_agent.query(message=msg.strip(), db=db, history=payload.history)


@router.post("/reorder", response_model=InventoryAction)
@alias_router.post("/reorder", response_model=InventoryAction)
def reorder_product(
    payload: InventoryReorderRequest,
    db: Session = Depends(get_db),
):
    """
    Triggers purchase order execution for restocking inventory.
    """
    return InventoryTools.execute_reorder(
        db=db,
        product_id=payload.product_id,
        quantity=payload.quantity,
        supplier_notes=payload.supplier_notes,
    )
