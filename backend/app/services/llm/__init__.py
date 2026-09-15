"""
LLM layer — LangChain-native.

    from app.services.llm import get_llm, get_chat_model
    llm = get_llm()                 # guarded free-form / structured calls
    model = get_chat_model()        # raw BaseChatModel for LangGraph / tool-calling

Providers (env `LLM_PROVIDER`, default `auto`): groq | openai | anthropic | bedrock | deterministic.
`auto` uses whichever key is present.
"""

from app.services.llm.chat_model import chat_model_status, get_chat_model, last_error
from app.services.llm.factory import get_llm, reset_llm_cache
from app.services.llm.guardrails import GuardedLLM, sanitize_untrusted, scrub_pii

__all__ = [
    "GuardedLLM",
    "chat_model_status",
    "get_chat_model",
    "get_llm",
    "last_error",
    "reset_llm_cache",
    "sanitize_untrusted",
    "scrub_pii",
]
