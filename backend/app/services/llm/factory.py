"""`get_llm()` — the process-wide guarded LLM singleton (LangChain-backed)."""

from __future__ import annotations

from functools import lru_cache

from app.core.logging import get_logger
from app.core.settings import settings
from app.services.llm.guardrails import GuardedLLM

logger = get_logger("llm")


@lru_cache
def get_llm() -> GuardedLLM:
    guarded = GuardedLLM()
    provider, model = settings.resolve_llm()
    logger.info(
        "llm_selected",
        configured=settings.LLM_PROVIDER,
        resolved=provider,
        model=model,
        available=guarded.available(),
    )
    return guarded


def reset_llm_cache() -> None:
    """Clear the cached model + guard (used after a settings change / in tests)."""
    get_llm.cache_clear()
    try:
        from app.services.llm.chat_model import get_chat_model

        get_chat_model.cache_clear()
    except Exception:
        pass
