"""
Assigns a request id + correlation id to every request, binds them into the
logging context, echoes `X-Request-ID` back, and logs one structured line per
request with method, path, status, and latency.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import correlation_id_ctx, get_logger, request_id_ctx

logger = get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        corr_id = request.headers.get("x-correlation-id") or req_id

        req_token = request_id_ctx.set(req_id)
        corr_token = correlation_id_ctx.set(corr_id)
        structlog.contextvars.bind_contextvars(request_id=req_id, correlation_id=corr_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            # Skip health probe noise
            if not request.url.path.startswith("/health"):
                logger.info(
                    "request",
                    method=request.method,
                    path=request.url.path,
                    status=status_code,
                    latency_ms=elapsed_ms,
                    client=request.client.host if request.client else None,
                )
            request_id_ctx.reset(req_token)
            correlation_id_ctx.reset(corr_token)
            structlog.contextvars.unbind_contextvars("request_id", "correlation_id")

    # BaseHTTPMiddleware cannot mutate headers on streaming responses reliably
    # for the id echo, so add it via a lightweight wrapper on the response.


async def add_request_id_header(request: Request, call_next):
    """Functional companion middleware to echo the id header (works with all response types)."""
    response = await call_next(request)
    rid = request_id_ctx.get()
    if rid:
        response.headers["X-Request-ID"] = rid
    return response
