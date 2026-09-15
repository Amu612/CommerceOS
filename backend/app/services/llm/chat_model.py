"""
LangChain chat-model factory.

Returns a `BaseChatModel` for the resolved provider, or `None` when no provider
is configured. This is what every LangGraph agent (`create_react_agent`),
LangChain tool-calling chain, and structured-output call binds to — so the whole
agent layer is LangChain-native and swaps providers by env alone.

Free-tier friendliness: hosted providers (Groq in particular) meter tokens per
minute, so every request is sent with an explicit completion budget
(`settings.LLM_COMPLETION_TOKEN_LIMIT`) and a small retry count that honours the
provider's `Retry-After` hint instead of surfacing a 429 to the user.
`chat_model_status()` also caches its reachability probe — the dashboard polls
it frequently and a live "ping" on every poll would burn the token budget that
real agent queries need.
"""

from __future__ import annotations

import time
from functools import lru_cache

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("llm.chat")

_STATE: dict = {"last_error": None}

# Reachability probe cache — holder dict avoids a module-level `global`.
# The ping costs real tokens on metered providers, so it runs at most once per
# TTL even when the dashboard polls every few seconds.
_STATUS_TTL_OK = 120.0
_STATUS_TTL_ERROR = 30.0
_status_cache: dict = {"entry": None, "expires_at": 0.0}  # {"entry": dict|None, "expires_at": float}


def last_error() -> str | None:
    return _STATE["last_error"]


def _groq_reasoning_effort() -> str | None:
    """gpt-oss models emit hidden reasoning tokens that consume a large share
    of the free-tier TPM budget; "low" trims them. ChatGroq (langchain-groq)
    takes this as a first-class constructor field, and only for models that
    support the parameter."""
    _, model = settings.resolve_llm()
    if settings.GROQ_REASONING_EFFORT and "gpt-oss" in (model or ""):
        return settings.GROQ_REASONING_EFFORT
    return None


@lru_cache
def get_chat_model():  # -> Optional[BaseChatModel]
    provider, model = settings.resolve_llm()
    _STATE["last_error"] = None

    try:
        if provider == "groq":
            if not settings.GROQ_API_KEY:
                return None
            from langchain_groq import ChatGroq

            return ChatGroq(
                model=model,
                api_key=settings.GROQ_API_KEY,
                temperature=0.2,
                max_retries=settings.LLM_MAX_RETRIES,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_tokens=settings.LLM_COMPLETION_TOKEN_LIMIT,
                reasoning_effort=_groq_reasoning_effort(),
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
                max_retries=settings.LLM_MAX_RETRIES,
                timeout=settings.LLM_TIMEOUT_SECONDS,
                max_tokens=settings.LLM_COMPLETION_TOKEN_LIMIT,
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
                max_tokens=settings.LLM_COMPLETION_TOKEN_LIMIT,
            )

        if provider == "bedrock":
            from langchain_aws import ChatBedrockConverse

            return ChatBedrockConverse(model=model, region_name=settings.BEDROCK_REGION, temperature=0.2)

    except ImportError as exc:  # pragma: no cover - optional deps
        _STATE["last_error"] = f"missing package for provider '{provider}': {exc}"
        logger.warning("chat_model_import_failed", provider=provider, error=str(exc))
    except Exception as exc:
        _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("chat_model_init_failed", provider=provider, error=str(exc))

    return None


def chat_model_status() -> dict:
    now = time.monotonic()
    cached = _status_cache["entry"]
    if cached is not None and _status_cache["expires_at"] > now:
        return cached

    provider, model = settings.resolve_llm()
    m = get_chat_model()
    ok = m is not None
    reachable = None
    if ok:
        try:
            m.invoke("ping")
            reachable = True
        except Exception as exc:
            reachable = False
            _STATE["last_error"] = f"invoke failed: {exc}"
    status = {
        "configured_provider": settings.LLM_PROVIDER,
        "resolved_provider": provider if ok else "deterministic",
        "model": model if ok else None,
        "chat_model_available": ok,
        "reachable": reachable,
        "last_error": _STATE["last_error"],
    }
    # A probe that errors may just be a transient 429 — retry sooner than a
    # healthy probe so the dashboard recovers quickly.
    ttl = _STATUS_TTL_OK if (ok and reachable) else _STATUS_TTL_ERROR
    _status_cache["entry"] = status
    _status_cache["expires_at"] = now + ttl
    return status
