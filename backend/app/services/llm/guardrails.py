"""
GuardedLLM — the single entrypoint every agent uses for free-form / structured
LLM calls. Delegates to a LangChain `BaseChatModel` (so the whole stack is
LangChain-native) and wraps it with:
  - tenacity retry + an in-process circuit breaker
  - a per-call output token ceiling from settings
  - prompt-injection stripping on untrusted input
  - PII scrub before anything is logged
  - `generate_structured(schema)` -> validated pydantic model or typed LLMException
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.logging import get_logger
from app.core.settings import settings
from app.exceptions.base import LLMException

logger = get_logger("llm.guard")

T = TypeVar("T", bound=BaseModel)

_INJECTION_PATTERNS = [
    re.compile(
        r"ignore\s+(?:all|any|the|your)?\s*(?:previous|prior|above)?\s*(?:instructions|prompts|rules)", re.I
    ),
    re.compile(r"disregard\s+(?:the|all|any|your)\s+(?:above|previous|system|instructions)", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an|in|the)\b", re.I),
    re.compile(r"</?\s*(?:system|assistant|user)\s*>", re.I),
    re.compile(r"\bsystem\s*prompt\b.*(?:reveal|show|print|repeat)", re.I),
]
_PII_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "<card>"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<ssn>"),
]


def scrub_pii(text: str) -> str:
    for pat, repl in _PII_PATTERNS:
        text = pat.sub(repl, text)
    return text


def sanitize_untrusted(text: str) -> str:
    cleaned = text
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("[removed]", cleaned)
    return cleaned


class _CircuitBreaker:
    def __init__(self, threshold: int = 4, cooldown_s: float = 30.0) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._failures = 0
        self._open_until = 0.0

    def before(self) -> None:
        if time.monotonic() < self._open_until:
            raise LLMException("LLM circuit breaker open — provider temporarily disabled.")

    def ok(self) -> None:
        self._failures = 0

    def fail(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._open_until = time.monotonic() + self.cooldown_s
            self._failures = 0
            logger.warning("llm_circuit_open", cooldown_s=self.cooldown_s)


class GuardedLLM:
    def __init__(self) -> None:
        self._breaker = _CircuitBreaker()

    # ── model access ──
    def _model(self):
        from app.services.llm.chat_model import get_chat_model

        return get_chat_model()

    def available(self) -> bool:
        try:
            return self._model() is not None
        except Exception:
            return False

    def _cap(self, n: int) -> int:
        return max(64, min(n, settings.LLM_REQUEST_TOKEN_BUDGET))

    # ── core ──
    @retry(
        reraise=True,
        stop=stop_after_attempt(1 + max(0, settings.LLM_MAX_RETRIES)),
        wait=wait_exponential(multiplier=0.5, max=6),
        retry=retry_if_exception_type(LLMException),
    )
    def _invoke(self, messages: list, *, max_tokens: int) -> str:
        self._breaker.before()
        model = self._model()
        if model is None:
            raise LLMException("No chat model configured.")
        try:
            bound = model.bind(max_tokens=self._cap(max_tokens)) if hasattr(model, "bind") else model
            resp = bound.invoke(messages)
            self._breaker.ok()
            text = getattr(resp, "content", None)
            if isinstance(text, list):  # some providers return content blocks
                text = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in text)
            usage = getattr(resp, "usage_metadata", None) or {}
            logger.info(
                "llm_call",
                model=getattr(model, "model_name", getattr(model, "model", "?")),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
            return (text or "").strip()
        except LLMException:
            raise
        except Exception as exc:
            self._breaker.fail()
            raise LLMException(f"LLM call failed: {exc}") from exc

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        untrusted: bool = True,
    ) -> str:
        if not self.available():
            return ""
        from langchain_core.messages import HumanMessage, SystemMessage

        safe_user = sanitize_untrusted(user) if untrusted else user
        return self._invoke(
            [SystemMessage(content=system), HumanMessage(content=safe_user)], max_tokens=max_tokens
        )

    def generate_json(
        self, *, system: str, user: str, max_tokens: int = 1200, untrusted: bool = True
    ) -> dict[str, Any] | None:
        if not self.available():
            return None
        text = self.generate_text(
            system=system + "\nRespond with ONE valid JSON object only. No markdown fences, no prose.",
            user=user,
            max_tokens=max_tokens,
            untrusted=untrusted,
        )
        return _loose_json(text)

    def generate_structured(
        self, *, system: str, user: str, schema: type[T], max_tokens: int = 1200, untrusted: bool = True
    ) -> T | None:
        if not self.available():
            return None
        model = self._model()
        try:
            structured = model.with_structured_output(schema)
        except Exception:
            raw = self.generate_json(system=system, user=user, max_tokens=max_tokens, untrusted=untrusted)
            if raw is None:
                return None
            try:
                return schema.model_validate(raw)
            except ValidationError as exc:
                raise LLMException(f"LLM output failed {schema.__name__} validation.") from exc

        from langchain_core.messages import HumanMessage, SystemMessage

        self._breaker.before()
        safe_user = sanitize_untrusted(user) if untrusted else user
        try:
            result = structured.invoke([SystemMessage(content=system), HumanMessage(content=safe_user)])
            self._breaker.ok()
            return result
        except Exception as exc:
            self._breaker.fail()
            raise LLMException(f"Structured LLM call failed: {exc}") from exc

    def stream_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 800,
        temperature: float = 0.2,
        untrusted: bool = True,
    ) -> Iterator[str]:
        if not self.available():
            return
        from langchain_core.messages import HumanMessage, SystemMessage

        self._breaker.before()
        model = self._model()
        safe_user = sanitize_untrusted(user) if untrusted else user
        try:
            for chunk in model.stream([SystemMessage(content=system), HumanMessage(content=safe_user)]):
                piece = getattr(chunk, "content", "")
                if isinstance(piece, list):
                    piece = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in piece)
                if piece:
                    yield piece
            self._breaker.ok()
        except Exception as exc:
            self._breaker.fail()
            raise LLMException(f"LLM stream failed: {exc}") from exc


def _loose_json(text: str) -> dict[str, Any] | None:
    import json

    s = (text or "").strip()
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
    s = s.strip().strip("`").strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        s = s[a : b + 1]
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None
