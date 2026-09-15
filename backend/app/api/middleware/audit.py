"""
Writes one `audit_logs` row for every authenticated, mutating (non-GET/HEAD/OPTIONS)
request. Failures here never break the request.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import get_logger, request_id_ctx
from app.core.security import decode_token

logger = get_logger("audit")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_SKIP_PREFIXES = ("/health", "/docs", "/openapi", "/redoc", "/metrics")


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)

        if request.method in _SAFE_METHODS or any(request.url.path.startswith(p) for p in _SKIP_PREFIXES):
            return response

        # Only audit authenticated calls.
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.query_params.get("token")
        payload = decode_token(token) if token else None
        if not payload:
            return response

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        try:
            from app.database.session import SessionLocal
            from app.services.auth_service import log_audit

            db = SessionLocal()
            try:
                log_audit(
                    db,
                    action=f"{request.method} {request.url.path}",
                    actor_id=payload.get("sub"),
                    actor_username=payload.get("username"),
                    actor_role=payload.get("role"),
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    ip_address=request.client.host if request.client else None,
                    request_id=request_id_ctx.get(),
                )
            finally:
                db.close()
        except Exception:
            logger.warning("audit_middleware_failed", path=request.url.path)

        return response
