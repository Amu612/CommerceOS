# ADR 0004 — `Agents/` is the canonical monorepo

**Status:** Accepted · **Date:** 2026-09-11

## Context
Three related codebases exist on disk:
1. `Agents/` — this repo (FastAPI + Vite, orders + inventory + customer agents).
2. `updated_CommerceOS/` — a sibling, same author/lineage, far more complete
   (6 agents + orchestrator + automation + auth/RBAC/audit + Redis + Postgres +
   Next.js + docker-compose).
3. Three reference zips (`sql-agent`, `langgraph-ecommerce-agent`,
   `agentic-ecommerce`) — pattern sources for text-to-SQL, evals, IaC, memory.

## Decision
- **`Agents/` is the single product repo.** All work lands here.
- **Port** proven modules from `updated_CommerceOS/` (auth service, orchestrator,
  automation, logistics/pricing/marketing agents, observability, exceptions,
  settings shapes) — adapting imports to this repo's `app.core.settings`,
  `app.services.llm`, and conventions.
- **Apply patterns** (not code) from the reference zips.
- Ported code is re-verified against the plan's acceptance criteria — the source
  is more complete but **not assumed correct or production-ready**. Known
  security holes in the source (e.g. `get_current_user` fabricating a user for an
  unknown token subject, `allow_origins=["*"]` with credentials) are **not** carried over.

## Consequences
- One CI/CD pipeline, one deployment, one docs set.
- `updated_CommerceOS/` becomes a read-only reference, not a dependency.
- Frontend stays Vite/React (not Next.js) to minimise churn; SSR is not required.
