"""
Typed application exceptions.

Every deliberately-raised error is one of these; the API error handlers turn them
into RFC-7807 problem responses. Unhandled exceptions become a generic 500 with
no internals leaked in production.
"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """Base class for all deliberate application errors."""

    status_code: int = 500
    error_code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        error_code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.error_code = error_code or self.error_code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationException(AppException):
    status_code = 401
    error_code = "unauthenticated"
    default_message = "Authentication required."


class AuthorizationException(AppException):
    status_code = 403
    error_code = "forbidden"
    default_message = "You do not have permission to perform this action."


class NotFoundException(AppException):
    status_code = 404
    error_code = "not_found"
    default_message = "Resource not found."


class ValidationException(AppException):
    status_code = 422
    error_code = "validation_error"
    default_message = "The request was invalid."


class ConflictException(AppException):
    status_code = 409
    error_code = "conflict"
    default_message = "The request conflicts with the current state."


class RateLimitException(AppException):
    status_code = 429
    error_code = "rate_limited"
    default_message = "Too many requests."

    def __init__(self, message: str | None = None, *, retry_after: int = 60, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class LLMException(AppException):
    status_code = 502
    error_code = "llm_error"
    default_message = "The AI provider failed to respond."


class AgentExecutionException(AppException):
    status_code = 500
    error_code = "agent_error"
    default_message = "The agent failed to complete its run."
