"""
Central exception handling. Registers handlers that emit RFC-7807-style
`application/problem+json` bodies and never leak stack traces in production.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.core.logging import get_logger, request_id_ctx
from app.core.settings import settings
from app.exceptions.base import AppException, RateLimitException

logger = get_logger("errors")


def _problem(status: int, code: str, title: str, detail: str, **extra) -> JSONResponse:
    body = {
        "type": f"about:blank#{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": request_id_ctx.get(),
    }
    body.update(extra)
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def _app_exc(_: Request, exc: AppException):
        headers = {}
        if isinstance(exc, RateLimitException):
            headers["Retry-After"] = str(exc.retry_after)
        logger.warning("app_exception", code=exc.error_code, status=exc.status_code, message=exc.message)
        resp = _problem(exc.status_code, exc.error_code, exc.error_code.replace("_", " ").title(), exc.message, **exc.details)
        for k, v in headers.items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(_: Request, exc: RequestValidationError):
        return _problem(
            422,
            "validation_error",
            "Validation Error",
            "One or more fields failed validation.",
            errors=exc.errors(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_: Request, exc: StarletteHTTPException):
        return _problem(exc.status_code, "http_error", "HTTP Error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("unhandled_exception", error=str(exc))
        detail = "An unexpected error occurred." if settings.is_production else f"{type(exc).__name__}: {exc}"
        return _problem(500, "internal_error", "Internal Server Error", detail)
