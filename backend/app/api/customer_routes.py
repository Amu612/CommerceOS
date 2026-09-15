"""
API routes for the Customer Support Agent (dynamic multi-agent workflow).

Endpoints (mirrors the E-Commerce AI Customer Support Agent project):
- GET  /api/customer/agents          -> agent manifest for the frontend
- POST /api/customer/query           -> full structured JSON response
- GET  /api/customer/stream?query=   -> SSE word-by-word stream (meta/chunk/done/error)

Aliased under /api/v1/agents/customer/* for parity with the Inventory agent.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.agents.customer import CustomerAgentResponse, customer_support_agent
from app.agents.customer.schemas import CustomerQueryRequest
from app.database.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/customer", tags=["Customer Support Agent"])
customer_router = router
alias_router = APIRouter(prefix="/api/v1/agents/customer", tags=["Customer Support Agent"])


@router.get("/agents")
@alias_router.get("/agents")
def get_agent_manifest():
    """Frontend-friendly manifest of the agents in the support pipeline."""
    return {"agents": customer_support_agent.manifest()}


@router.post("/query", response_model=CustomerAgentResponse)
@alias_router.post("/query", response_model=CustomerAgentResponse)
def query_customer_agent(payload: CustomerQueryRequest, db: Session = Depends(get_db)):
    """Runs the full multi-agent pipeline and returns the structured result."""
    msg = (payload.query or payload.message or "").strip()
    return customer_support_agent.query(message=msg, db=db, history=payload.history)


async def _sse_stream(query: str):
    """Streams the pipeline's final response word-by-word in SSE format."""
    try:
        if query.lower().strip() in ("exit", "bye", "goodbye", "good bye", "good day", "quit"):
            yield f"data: {json.dumps({'type': 'done', 'chunk': 'Thank you for contacting support. Have a great day!', 'done': True})}\n\n"
            return

        result: CustomerAgentResponse = await run_in_threadpool(customer_support_agent.query, query)
        text = result.final_response or result.response or "No response generated."

        yield f"data: {json.dumps({'type': 'meta', 'category': result.category, 'agents': result.agents_involved})}\n\n"

        words = text.split()
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            yield f"data: {json.dumps({'type': 'chunk', 'chunk': chunk, 'done': False})}\n\n"
            await asyncio.sleep(0.02)

        yield f"data: {json.dumps({'type': 'done', 'chunk': '', 'done': True})}\n\n"
    except Exception:
        logger.exception("customer stream pipeline failed")
        yield f"data: {json.dumps({'type': 'error', 'message': 'Internal server error', 'done': True, 'error': True})}\n\n"


@router.get("/stream")
@alias_router.get("/stream")
async def stream_customer_agent(query: str):
    return StreamingResponse(
        _sse_stream(query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
