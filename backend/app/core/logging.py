"""
Structured logging for the platform.

- JSON in production/compose, pretty console in local dev.
- Every log line carries `request_id` and `correlation_id` when available
  (set by `RequestContextMiddleware`) plus any `execution_id` bound by an agent run.
- Standard-library logging is routed through structlog so third-party logs
  (uvicorn, sqlalchemy) share the same format.
"""
from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, MutableMapping

import structlog

from app.core.settings import settings

# Populated per-request by RequestContextMiddleware and per-run by agents.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)
execution_id_ctx: ContextVar[str | None] = ContextVar("execution_id", default=None)

_CONFIGURED = False


def _context_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key, ctx in (
        ("request_id", request_id_ctx),
        ("correlation_id", correlation_id_ctx),
        ("execution_id", execution_id_ctx),
    ):
        value = ctx.get()
        if value is not None:
            event_dict.setdefault(key, value)
    return event_dict


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _context_processor,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.LOG_JSON:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging through structlog.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=renderer,
            foreign_pre_chain=shared_processors,
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy in ("uvicorn.access", "httpx", "httpcore", "python_multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _CONFIGURED:
        configure_logging()
    return structlog.get_logger(name)
