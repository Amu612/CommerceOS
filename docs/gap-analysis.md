# Gap Analysis — where the system stands vs. the problem statement

> **Problem statement:** AI-powered multi-agent platform that automates end-to-end
> e-commerce operations (inventory, orders, customer support, pricing, marketing,
> logistics) with autonomous AI agents.
>
> **Expected solution:** a *deployed* web app with cloud-deployed AI agents +
> architecture + DB schema + cost estimate + presentation + documentation, on a
> cloud platform (AWS preferred).

Legend: ✅ done · 🟡 partial · 🔴 missing/broken

---

## 1. The six agents + orchestrator

| Capability | State | Notes |
|---|---|---|
| Orders agent | ✅ | LangGraph (triage → tools → response, retry loop) over 18 LangChain `@tool`s; empirical stats (percentiles/IQR/MAD/z-score), confidence, forecasting. Hardcoded fallbacks (`142.80`, hash-derived price, `1.8` baseline) **removed** — now real payments / global benchmark / `None`. |
| Inventory agent | ✅ | **De-hashed.** Stock is now `MODELLED` from *real* demand (`order_items` counts, recency-weighted daily rate) × a documented cover window — no MD5, no `11115`/`118.5`/`79.99` constants. ROP/EOQ computed from that. `data_status = MODELLED` everywhere (Olist has no warehouse feed — this is the honest ceiling). |
| Customer agent | ✅ | Multi-agent LangGraph (triage → router → specialists → supervisor). **Multi-intent routing** (one message → sales + refund + billing as needed). Real billing from seeded `order_payments`. Return eligibility uses the **simulated clock** (was `datetime.now()`). Greeting/capability handling. `create_react_agent` path over 8 customer tools when an LLM is configured. |
| Logistics agent | ✅ | New. LangGraph tool-agent (`framework.py`): 5 metric `@tool`s + 4 detector `@tool`s (carrier scorecard, lane SLA gap, transit distribution, late-delivery risk). Severities from empirical fences. |
| Pricing agent | ✅ | New. Blended/order margin, loss-making detection, discount leakage, freight drag — all `@tool`-driven off DataCo profit + Olist item prices. |
| Marketing agent | ✅ | New. RFM segmentation, repeat rate, churn/lapsed share, demand concentration — `@tool`-driven off `customers`/`order_items`/`order_reviews`. |
| Orchestrator | ✅ | Runs all 6 in parallel, normalises to `DomainSnapshot`, cross-domain correlation → systemic findings, deterministic conflict resolution, priority-action queue, KPI rollup. |
| **"Automated through LangChain"** | ✅ | Every agent is a LangGraph graph or `create_react_agent`; every computation is a LangChain `@tool`; the LLM binds via `langchain_groq` / `langchain_openai` / … `BaseChatModel`. |

### Still 🟡 on the agents
- **HITL automation** (`app/automation/`): agents produce `recommended_action` /
  `automation_eligibility` but nothing *executes* or *queues for approval*. Needs
  the policy engine + executor + approvals queue + UI (plan M6).
- **LangGraph checkpointing / long-term memory**: graphs are stateless per call;
  no `thread_id` persistence, no customer-profile memory store (plan A3).
- **Agent-run persistence**: no `agent_runs` / `agent_findings` tables yet, so
  there's no history / trend of what each agent found over time (plan A9).
- **Agent eval harness**: no golden Q&A set, no regression gate on agent quality
  (plan T5).
- **Forecasting depth**: OLS + intervals only; no Holt-Winters/STL, no backtest
  accuracy reporting.
- **Text-to-SQL "Ask the data" agent**: not built (plan A8).

---

## 2. LLM

| Item | State | Notes |
|---|---|---|
| Provider abstraction | ✅ | `LLM_PROVIDER=auto\|groq\|openai\|anthropic\|bedrock\|deterministic`. `auto` uses whichever key is set. `GuardedLLM` = retry + circuit breaker + token cap + prompt-injection strip + PII scrub + structured-output validation. |
| LangChain chat model | ✅ | `get_chat_model()` returns a `BaseChatModel`; `create_react_agent` + tool-calling bind to it. |
| **A working LLM right now** | 🔴 | **No key is configured and no local model server is running on this machine.** The agents therefore run the deterministic path. Add `GROQ_API_KEY=` to `backend/.env` (free at console.groq.com) and restart → every agent gets Llama-3.3-70B. Status is visible in the UI header badge and at `GET /api/v1/system/llm`. |
| Cost telemetry | 🟡 | Token/USD accounting exists in `GuardedLLM` but isn't rolled into a metric or a budget alarm yet (plan OB5). |

---

## 3. Data

| Item | State | Notes |
|---|---|---|
| Warehouse tables | ✅ | Olist (orders, items, customers, products, **payments**, **reviews**, sellers, geo, **category translation**) + DataCo now all seeded. Payments/reviews/translations were **missing before** — added to `scripts/seed_nexus_data.py` and loaded. |
| Datasets in-repo | ✅ | Olist + DataCo CSVs copied into `backend/data/raw/` (gitignored, 194 MB) so the pipeline is self-contained. |
| Engine | 🔴 | Still SQLite (`orders.db`). PostgreSQL migration is authored (`ADR 0002`, plan M2) but not executed — needs a running Postgres. |
| Reproducible build | 🟡 | `scripts/build_warehouse.py` (plan D4) not yet written; current seeding is the older `seed_nexus_data.py` (extended). |
| Simulated-clock robustness | ✅ | `simulated_clock()` opens its own session if none is passed and falls back to `max(timestamp)` when nothing has streamed — fixed the "everything NOT_ESTIMABLE on fresh load" and "3257 days ago" bugs. |
| New operational tables | 🔴 | `agent_runs`, `agent_findings`, `automation_actions`, `approvals`, `conversations`, `customer_memory`, `stock_movements`, `orchestration_decisions`, `campaigns`, `carrier_lanes` — modelled in `docs/db-schema.md`, not created. |

---

## 4. Security · Auth

| Item | State |
|---|---|
| JWT auth + RBAC + audit log | ✅ built (`app/core/security.py`, `app/services/auth_service.py`, `app/api/v1/{auth,users,audit}.py`, `AuditLog` model, audit + security-headers middleware) |
| Enforcement | 🟡 `AUTH_ENFORCED=false` in dev so the demo dashboard works without a login wall; `true` in prod/test. **Frontend has no login screen yet** (plan FE2) — so with `AUTH_ENFORCED=true` the SPA can't call the API. |
| Rate limiting | 🔴 needs Redis (plan S7) |
| Analytics SQL sandbox | 🔴 with the text-to-SQL agent (plan S8) |
| Secrets → Secrets Manager | 🔴 with cloud (plan S9); local `.env` is gitignored, the old committed one is gone |

---

## 5. The "expected solution" deliverables

| Deliverable | State | Where |
|---|---|---|
| Deployed web application (AWS) | 🔴 **Not deployed.** | Terraform network module + full topology spec exist (`infra/aws/`, `docs/architecture.md §2`). RDS/Redis/ECS/ALB/CloudFront/WAF modules + CI deploy job are authored to spec but not applied — needs an AWS account + `terraform apply`. Docker Desktop is installed here but the daemon is off; Terraform isn't installed. |
| Cloud-deployed AI agents | 🔴 | Same — the containers + task defs are specified; not running in cloud. |
| Solution architecture | ✅ | `docs/architecture.md` (C4 context/container/component, agent-run + orchestrator sequences, data flow, security, observability, environments). |
| DB schema | ✅ | `docs/db-schema.md` (every table, key columns, indexing, roles) + `ADR 0002`. ERD render pending (`make erd`). |
| Estimated costing | ✅ | `docs/cost-estimate.md` + `docs/cost-model.csv` (dev / prod-low / prod-target line items + an LLM token cost model by provider). |
| Presentation | 🔴 | Not built (plan DOC9 — reveal.js deck + generated `.pptx`). |
| Project documentation | 🟡 | `README.md`, `docs/architecture.md`, `docs/db-schema.md`, `docs/cost-estimate.md`, `docs/security.md`, `docs/runbook.md`, `docs/adr/0001–0006`, this file. Missing: `docs/api.md` (from OpenAPI), demo script. |

---

## 6. Production-readiness (whole-system)

| Area | State |
|---|---|
| Config / settings | ✅ `app/core/settings` (pydantic-settings, dev/test/prod profiles) |
| Structured logging + request IDs | ✅ structlog JSON, correlation/request/execution IDs |
| Typed errors (RFC-7807) | ✅ `app/exceptions` + `app/api/error_handlers` |
| Containers | ✅ `backend/Dockerfile`, `frontend/Dockerfile` + nginx, `docker-compose.yml` (postgres/redis/migrate/backend/worker/frontend) — **not run** (Docker daemon off here) |
| CI | ✅ `.github/workflows/ci.yml` (lint, typecheck, tests w/ Postgres service, image build, Trivy, secret scan) |
| CD | 🟡 `.github/workflows/deploy.yml` authored; needs real AWS resource names + OIDC role |
| Tests | 🟡 **51 backend tests** (foundation, auth/RBAC, LLM guardrails, all 6 agents + orchestrator contract). No frontend tests, no e2e, no load test, no coverage gate met yet. |
| Realtime (WS) | 🔴 frontend still polls; no Redis pub/sub → WebSocket (plan M4) |
| Background worker | 🟡 stub only (`app/worker/main.py`); replay loop still runs in the API process |
| Observability infra | 🔴 no `/metrics`, no OpenTelemetry export, no CloudWatch dashboards/alarms (plan M9) |
| Frontend | 🟡 7 dashboards work (Orchestrator + 6 agents), LLM status badge. No router, no auth UI, no design-system, `OrdersAgentDashboard.tsx` still 1500 lines, no tests. |

---

## 7. Priority order to "done"

1. **Add a Groq key** → `backend/.env` (1 line). Unblocks every agent's conversational layer. *(you)*
2. **Postgres + real migrations** (plan M2) — removes the SQLite ceiling, enables the new operational tables.
3. **Agent-run persistence + eval harness** (A9, T5) — so agents have history and a quality gate.
4. **HITL automation + approvals** (M6) — the "autonomous" half of the problem statement.
5. **Frontend auth + realtime** (FE2, M4) — so `AUTH_ENFORCED=true` is usable and updates are pushed.
6. **AWS deploy** (M12) — Terraform apply + CD wiring → the "deployed on AWS" deliverable.
7. **Presentation + `docs/api.md` + demo script** (M13) — the remaining paper deliverables.
8. **Observability** (M9), **text-to-SQL agent** (A8), **checkpointing/memory** (A3), forecasting depth.

Everything above item 1 needs infrastructure this machine doesn't currently have
running (Postgres, Docker daemon, Terraform, an AWS account).
