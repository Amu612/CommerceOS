"""System / platform status endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.settings import settings
from app.database.session import check_db
from app.services.llm import chat_model_status

router = APIRouter(prefix="/api/v1/system", tags=["System"])


@router.get("/llm")
def llm_status():
    """Which LLM provider is active, whether it is reachable, and the last error if any."""
    return chat_model_status()


@router.get("/status")
def platform_status():
    llm = chat_model_status()
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "database_ok": check_db(),
        "llm": llm,
        "auth_enforced": settings.AUTH_ENFORCED,
    }
