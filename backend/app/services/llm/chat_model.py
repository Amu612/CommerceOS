"""
LangChain chat-model factory.

Returns a `BaseChatModel` for the resolved provider, or `None` when no provider
is configured. This is what every LangGraph agent (`create_react_agent`),
LangChain tool-calling chain, and structured-output call binds to — so the whole
agent layer is LangChain-native and swaps providers by env alone.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("llm.chat")

_LAST_ERROR: Optional[str] = None


def last_error() -> Optional[str]:
    return _LAST_ERROR


@lru_cache
def get_chat_model():  # -> Optional[BaseChatModel]
    global _LAST_ERROR
    provider, model = settings.resolve_llm()
    _LAST_ERROR = None

    try:
        if provider == "groq":
            if not settings.GROQ_API_KEY:
                return None
            from langchain_groq import ChatGroq

            return ChatGroq(
                model=model,
                api_key=settings.GROQ_API_KEY,
                temperature=0.2,
                max_retries=0,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )

        if provider == "openai":
            key = settings.OPENAI_API_KEY or settings.GEMINI_WEB2API_API_KEY
            base = settings.OPENAI_API_BASE
            if settings.GEMINI_WEB2API_BASE_URL and not settings.OPENAI_API_KEY:
                base = settings.GEMINI_WEB2API_BASE_URL
                model = settings.GEMINI_WEB2API_MODEL or model
            if not key:
                return None
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model,
                api_key=key,
                base_url=base,
                temperature=0.2,
                max_retries=0,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )

        if provider == "anthropic":
            if not settings.ANTHROPIC_API_KEY:
                return None
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model,
                api_key=settings.ANTHROPIC_API_KEY,
                temperature=0.2,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )

        if provider == "bedrock":
            from langchain_aws import ChatBedrockConverse

            return ChatBedrockConverse(model=model, region_name=settings.BEDROCK_REGION, temperature=0.2)

    except ImportError as exc:  # pragma: no cover - optional deps
        _LAST_ERROR = f"missing package for provider '{provider}': {exc}"
        logger.warning("chat_model_import_failed", provider=provider, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        logger.warning("chat_model_init_failed", provider=provider, error=str(exc))

    return None


def chat_model_status() -> dict:
    provider, model = settings.resolve_llm()
    m = get_chat_model()
    ok = m is not None
    reachable = None
    if ok:
        try:
            m.invoke("ping")
            reachable = True
        except Exception as exc:  # noqa: BLE001
            reachable = False
            globals()["_LAST_ERROR"] = f"invoke failed: {exc}"
    return {
        "configured_provider": settings.LLM_PROVIDER,
        "resolved_provider": provider if ok else "deterministic",
        "model": model if ok else None,
        "chat_model_available": ok,
        "reachable": reachable,
        "last_error": _LAST_ERROR,
    }
