# ADR 0005 — LangGraph agents + a coordinating orchestrator

**Status:** Accepted · **Date:** 2026-09-11

## Context
Each domain (orders, inventory, customer, logistics, pricing, marketing) needs an
autonomous agent that observes live data, runs deterministic analysis tools,
reasons about findings, and produces evidence-backed recommendations. The
problem statement asks for a *platform* — the domains also interact (a
cancellation spike touches orders, customer, and marketing at once).

## Decision
- **Domain agents** are `langgraph.StateGraph` machines:
  `observe → triage/plan → execute deterministic tools → analyse → assess risk →
  recommend`, with a bounded self-correction / retry loop. All arithmetic is in
  LangChain `@tool` functions + `app/intelligence/*` — **the LLM never does math**.
- **The analytics agent** is a separate NL→SQL graph (reformulate → classify →
  CoT-plan → generate → execute-with-guardrail → self-correct → summarise),
  patterned on the `sql-agent` reference.
- **The orchestrator** (`app/orchestrator/`) is not a 7th business agent. It:
  routes an event to the relevant domain agents, collects their findings/risks,
  runs cross-domain correlation, resolves conflicting recommendations
  deterministically (with a recorded rationale), and emits one
  `orchestration_decisions` record + notifications.
- Graphs use a **Postgres checkpoint saver** and a `thread_id` per
  conversation/run for resumability; long-term memory (customer profiles) is a
  `BaseStore` in Postgres.

## Consequences
- Deterministic outputs are reproducible and auditable; LLM is used only for
  intent classification, natural-language synthesis, and SQL generation.
- The orchestrator is testable in isolation with stubbed domain agents.
- Adding a domain = add one `app/agents/<domain>/` package + register it with the
  orchestrator's `EventRouter`.
