# ADR 0003 — LLM access via a provider abstraction

**Status:** Accepted · **Date:** 2026-09-11

## Context
The pre-existing `llm_service.py` hard-coded Gemini/OpenAI/Groq HTTP calls with
`urllib`, no retries, no circuit breaker, no cost accounting, no output validation,
and no prompt-injection handling. It also silently returned `None` when no key was
configured, so **every agent ran its deterministic fallback in practice**. The
`.env` only contained keys the code never read.

## Decision
Introduce `app/services/llm/`:
- `LLMProvider` interface (`available`, `generate_text`, `generate_json`,
  `stream_text`) with adapters: `deterministic` (default, no network),
  `openai`, `anthropic`, `bedrock` (AWS Converse).
- `GuardedLLM` wraps every provider: tenacity retry + in-process circuit breaker,
  per-call token-budget cap (`LLM_REQUEST_TOKEN_BUDGET`), untrusted-input
  prompt-injection stripping, PII scrub before logging, and
  `generate_structured(schema)` that validates output against a pydantic model
  and raises a typed `LLMException` on failure.
- Selected by `LLM_PROVIDER` env. `factory.get_llm()` is a cached singleton.
- Agents depend only on `get_llm()` — never a vendor SDK.

## Consequences
- Deterministic mode is a first-class provider, so agent code has one path shape.
- Cost is measured per call (`LLMUsage.cost_usd`) and rolls up into
  `agent_runs` + a `llm_cost_usd_total` metric + a monthly budget alarm.
- Swapping Bedrock ↔ OpenAI is an env change, no code change.
- `llm_service.py` remains only as a deprecated shim during agent migration (A2).
