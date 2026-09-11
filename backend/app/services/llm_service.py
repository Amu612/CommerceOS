"""
DEPRECATED compatibility shim.

The real implementation is `app.services.llm` (provider-abstracted + guardrailed).
This module keeps the old `llm_service.is_available() / generate_text / generate_json`
surface so existing agents keep working until migrated (plan task A2).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.llm import get_llm


class _LLMServiceShim:
    def is_available(self) -> bool:
        return get_llm().available()

    def generate_text(
        self, system_prompt: str, user_message: str, max_tokens: int = 1000
    ) -> Optional[str]:
        text = get_llm().generate_text(
            system=system_prompt, user=user_message, max_tokens=max_tokens, untrusted=True
        )
        return text or None

    def generate_json(self, system_prompt: str, user_message: str) -> Optional[Dict[str, Any]]:
        return get_llm().generate_json(system=system_prompt, user=user_message, untrusted=True)


llm_service = _LLMServiceShim()
