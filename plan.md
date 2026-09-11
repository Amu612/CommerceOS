# CommerceOS / Nexus — Production Readiness Plan

> **Problem statement:** Build an AI-powered multi-agent platform that automates end-to-end
> e-commerce operations (inventory, orders, customer support, pricing, marketing, logistics)
> using autonomous AI agents.
>
> **Expected solution:** A deployed web application with cloud-deployed AI agents, solution
> architecture, DB schema, estimated costing, presentation, and project documentation. Must be
> **fully deployed on a cloud platform (AWS preferred)**.

This document is the single source of truth for the remaining work. It is written to be
executed top-to-bottom. Every task is file/module specific and has an acceptance criterion.

---

## Implementation progress log

| Date | Milestone | Status |
|---|---|---|
| 2026-09-11 | **M1 Foundation** | ✅ **Done & verified.** `backend/requirements.txt` + `requirements-dev.txt` + `pyproject.toml` (ruff/black/isort/mypy/pytest/coverage). `app/core/settings/` (pydantic-settings, dev/test/prod profiles, all `os.environ` reads centralised for `main.py`/`session.py`). `app/core/logging.py` (structlog JSON, request-id/correlation-id/execution-id contextvars). `app/api/middleware/request_context.py` (per-request id + structured access log). `app/exceptions/` + `app/api/error_handlers.py` (typed exceptions → RFC-7807 problem+json, no stack traces in prod). Root `.gitignore` (+ `git init`), `backend/.env.example`, `frontend/.env(.example)`, removed committed `backend/.env` + `llm_service.py.backup`. `backend/Dockerfile` (multi-stage, non-root, healthcheck) + `frontend/Dockerfile` + `nginx.conf` + `.dockerignore`s. Root `docker-compose.yml` (postgres/redis/migrate/backend/worker/frontend). `Makefile`. `.pre-commit-config.yaml` + `.secrets.baseline`. `app/worker/main.py` stub. `session.py` reworked: Postgres pool config, `get_read_db`, `check_db()`, `/health` + `/health/ready`. **23 existing tests still green.** |
| 2026-09-11 | **M3 Security (core)** | ✅ **S1, S2, S3, S5 done & verified; S6 partial.** `app/core/security.py` (bcrypt + python-jose HS256, access/refresh). `app/services/auth_service.py` (authenticate, create_user, `log_audit`). `app/models/security.py`: added `AuditLog`, `customer` key in `AGENT_ROLE_MAP`. `app/api/v1/{deps,auth,users,audit}.py` — `get_current_user` (no fabricated users), `require_super_admin`/`require_roles`/`require_agent_access`, `POST /api/v1/auth/{login,refresh,logout}` + `GET /me`, user admin, audit read. `app/api/middleware/audit.py` (one audit row per authenticated mutation) + `security_headers.py`. **All legacy routers now require a JWT** (`main.py` `dependencies=[Depends(get_current_user)]`); `/health*` + `/api/v1/auth/login` public. `scripts/seed_users.py` (7 role users, idempotent). `tests/conftest.py` (auth-override fixture, T1 seed). `tests/test_auth.py` (8 tests: hash/jwt round-trips, login, 401 without token, RBAC 403). **31 tests green.** Remaining in M3: S4 audit-log migration table (model added, needs D2 migration), S6 body-size limit, S7 rate limiting (needs Redis/R1), S8 (with A8), S9 (with M12), S10 (with A1). |

| 2026-09-11 | **M5 / A1 LLM provider abstraction** | ✅ **Done & verified.** `app/services/llm/` — `base.py` (`LLMProvider` iface, `LLMResult`/`LLMUsage`, loose-JSON parse), `deterministic.py` (default, no network), `openai_provider.py`, `anthropic_provider.py`, `bedrock_provider.py` (Converse API), `pricing.py` ($/1k table for cost telemetry), `guardrails.py` (`GuardedLLM`: tenacity retry, in-proc circuit breaker, per-call token-budget cap, prompt-injection stripping on untrusted input, PII scrub, `generate_structured` pydantic validation → typed `LLMException`), `factory.py` (`get_llm()` singleton + legacy-key bridge). `llm_service.py` reduced to a thin deprecated shim delegating to the new layer (agents unchanged, migrated in A2). `tests/test_llm.py` (7 tests: loose-json, pii/injection, budget cap, retry, breaker, structured validation, soft-unavailable). **38 tests green.** |

| 2026-09-11 | **M11 CI/CD (C1 core)** | 🟡 **Authored.** `.github/workflows/ci.yml` (PR + push: backend ruff/black/isort + mypy(advisory) + pytest w/ Postgres service + coverage.xml + pip-audit + detect-secrets; frontend eslint + typecheck + vitest + build; image build + Trivy scan). `.github/workflows/deploy.yml` (OIDC → ECR push → one-shot migrate ECS task → rolling `update-service` → smoke `/health` + `/health/ready` → auto-rollback to previous task def). `.gitattributes` (LF normalization). Needs the eval gate (T5) and real AWS resource names once M12 lands. |
| 2026-09-11 | **M12 Infra (I1/I2 partial)** | 🟡 **Started.** `infra/aws/README.md` (module layout + deploy flow). `infra/aws/modules/network/` — full VPC module: 2-AZ public/private subnets, single/per-AZ NAT, gateway + interface VPC endpoints (ecr/secrets/logs/ssm/**bedrock-runtime**/s3/dynamodb), reject-traffic flow logs. Remaining modules (rds, redis, ecs, alb, frontend_cdn, iam, secrets, observability, waf) + `envs/{dev,prod}` wiring are specified in `docs/architecture.md` §2 + `docs/cost-estimate.md` and are the bulk of remaining M12. |
| 2026-09-11 | **M13 Docs (DOC1–DOC4, DOC6–DOC8)** | ✅ **Done.** `README.md` rewritten (overview, all 8 agents, compose + bare quickstart, datasets, config table, deploy, docs index). `docs/architecture.md` (C4 context/container/component mermaid, agent-runtime graph, API sequence, ingestion/replay flow, security + observability + environments + decisions). `docs/db-schema.md` (every table incl. the new operational/agent/memory tables, indexing, roles, conventions). `docs/cost-estimate.md` + `docs/cost-model.csv` (dev / prod-low / prod-target line items + LLM token cost model by provider + cost controls). `docs/security.md` (STRIDE table, RBAC matrix, PII inventory, secret rotation, responsible-AI). `docs/runbook.md` (deploy/rollback/PITR restore/RO-role bootstrap/secret rotation/scale/replay ops/incident triage/history scrub). `docs/adr/0001-0006` (AWS, Postgres, LLM abstraction, canonical monorepo, LangGraph+orchestrator, HITL automation). Remaining: DOC3 ERD render (needs D2 + eralchemy), DOC5 api.md (needs P1), DOC9 presentation deck, DOC10 demo assets. |

**Not yet started:** M2 (Postgres migrations / warehouse loader — needs live Postgres to fully verify), M4 (realtime), M5 A2–A9 (migrate agents to `llm` layer, checkpointing/memory, logistics/pricing/marketing/analytics agents, run persistence), M6 (orchestrator/automation), M7 (API consolidation), M8 (frontend rebuild), M9 (observability), M10 (T2+), remaining M11/M12, M13 (ERD, api.md, presentation, demo assets).

| 2026-09-11 | **Agent restoration + M5 A4/A6 + M6 (orchestrator core)** | ✅ **Done & verified in-browser.** Root cause of the "all agents broken / Failed to fetch" screenshots: M3 added JWT auth to every route but the SPA has no login yet. Fix: `settings.AUTH_ENFORCED` (soft-auth in dev via `auth_dependency()` → `get_optional_user`; strict in prod/test; conftest forces strict). Second fix: agents were pinned to the dataset *start* clock (2015) so a fresh load saw ~no data — `simulated_clock()` now falls back to `max(timestamp)` when nothing has been streamed. **New agents** (`app/agents/{logistics,pricing,marketing}/` — `data_layer.py` + `agent.py` + `__init__.py`, `common_schemas.py`, `_shared.py`): Logistics (carrier/lane SLA, transit distribution, late-delivery risk), Pricing (blended/order margin, loss-making detection, discount leakage, freight drag), Marketing (RFM segmentation, repeat rate, demand concentration, campaign plays). All deterministic + optional LLM narration; rich topic-routed `deterministic_answer()` handles help/topic/why/recommend/overview intents. **Orchestrator** (`app/orchestrator/` — `coordinator.py` runs all 6 domains in parallel, normalises to `DomainSnapshot`, cross-domain correlation → `SystemicFinding`, deterministic `ConflictResolver`, priority-action queue, KPI rollup, optional LLM briefing). **API**: `app/api/v1/agents/__init__.py` (logistics/pricing/marketing `/analyze` + `/query`), `app/api/v1/orchestrator.py` (`/run`). **LLM bridge**: `factory.get_llm()` now auto-adopts a local OpenAI-compatible proxy (`GEMINI_WEB2API_*` or `OPENAI_API_BASE`) — no code change to switch on a real LLM. **Frontend**: `DomainAgentView.tsx` (generic renderer: metrics, findings w/ evidence, recommendations, chart tables, chat), `OrchestratorView.tsx` (6-domain grid + systemic findings + conflicts + priority queue + KPIs), `App.tsx` rewritten to a 7-tab config-driven nav (**Orchestrator · Orders · Inventory · Logistics · Pricing · Marketing · Customer**). `backend/.env` created with LLM options documented. **11 new backend tests** (`test_domain_agents.py`) — 49 total green; `tsc` + `vite build` clean; **all 7 tabs verified working in the browser** with real data-derived findings; orchestrator produces 2 systemic findings + 2 resolved conflicts on the current dataset. |

| 2026-09-11 | **PostgreSQL + HITL automation + frontend auth/WebSocket** | ✅ **Done & verified end-to-end.** **PostgreSQL + Redis live**: Docker containers `commerceos-pg` / `commerceos-redis`; `alembic upgrade head` (baseline `9ebc4da1d161`, 26 tables) run against Postgres; `seed_nexus_data.py` seeded ~20k orders + sellers/products/payments/reviews/translations (added FK-safe bulk-insert of missing products/sellers before `order_items` — Postgres enforces FKs, SQLite didn't). `session.py` only `create_all` on SQLite; Postgres schema is migration-owned. `backend/.env` → `DATABASE_URL=postgresql://…`, `REDIS_ENABLED=true`, `AUTH_ENFORCED=true`. **HITL automation executes** (`app/automation/` — `policies.py` `Mode.AUTO|NEEDS_APPROVAL|BLOCKED` per action type + min-confidence; `actions.py` `HANDLERS` execute/verify/rollback; `executor.py` `propose_for_run()` de-dupes → AUTO actions self-execute + verify, others become `Approval` rows routed by `required_role`; `decide()` executes + `log_audit` + publishes event). Wired into every agent `/analyze` (`agent_run_service.record_analysis`) and `/orchestrator/run` (`_persist`). `app/models/operations.py` (`AgentRun`/`AgentFinding`/`OrchestrationDecision`/`AutomationAction`/`Approval`/`Conversation*`/`CustomerMemory`/`StockMovement`). New APIs: `GET /api/v1/automation/{actions,approvals}`, `POST …/approvals/{id}/{approve,reject}` (RBAC-gated), `GET /api/v1/runs`, `GET /api/v1/orchestrator/{latest,decisions}`, `GET /api/v1/system/{llm,status}` (public). **WebSocket** `GET /api/v1/stream/ws?token=` — Redis `commerceos:events` pubsub fan-out + in-process fallback, query-token auth when `AUTH_ENFORCED`. **Frontend auth + live updates**: `lib/api.ts` (`apiFetch`, `login`, session store, `installFetchAuth()` global interceptor attaches JWT to every API call + routes 401s), `auth/AuthContext.tsx` + `pages/Login.tsx` (login gate keyed off `/system/status.auth_enforced`), `ApprovalsView.tsx` (8th tab — pending-approval cards with approve/reject, auto-executed/blocked KPIs, recent-actions table), `lib/ws.ts` (`subscribeEvents` auto-reconnect). `App.tsx`: login gate, user chip + logout, WS-driven toast + cheap `liveKey` refresh. **Feedback-loop fix**: `/orchestrator/run` was GET + auto-polled on every refresh tick, and agent `/analyze` + orchestrator now emit events → WS bumped refresh → re-ran analysis → looped until Groq 429 + circuit-breaker 502. Fixed: `/run` is POST-only (explicit "Run Sweep"), dashboards poll new cheap `/orchestrator/latest` (last persisted sweep, ~0.2s); WS events bump only `liveKey` (cheap re-reads), never the expensive `refreshKey`; streaming re-analysis throttled 2s→12s; orchestrator LLM briefing failure degrades to deterministic summary instead of 502. **55 backend tests green** (+4: `/latest` cheap-read, automation propose→approve, public `/system/status`); `tsc` + `vite build` clean. **Verified in-browser**: login → all 8 tabs → LLM badge "GROQ" → Approvals tab approve (→ EXECUTED + audit row) → WS toast on sweep completion; no request storm; all 5 agent LLM chats + orchestrator return data-grounded answers (`llm_backed: true`). |

| 2026-09-11 | **LangChain-native agent rebuild + LLM + data-driven fixes** | ✅ **Done & verified.** **LLM layer rewritten to be LangChain-native**: `app/services/llm/chat_model.py` (`get_chat_model()` → `ChatGroq`/`ChatOpenAI`/`ChatAnthropic`/`ChatBedrockConverse`), `GuardedLLM` now delegates to a `BaseChatModel`; `LLM_PROVIDER=auto` picks whichever key is set; old bespoke provider adapters deleted. Installed `langchain-groq` + `langchain-openai` (fixed langchain-core version conflict). `GET /api/v1/system/{llm,status}` + a header **LLM status badge**. **Logistics / Pricing / Marketing rebuilt** on a shared `app/agents/framework.py` (`DomainAgent`): findings now come from LangChain `@tool` **detector** functions (empirical severities, no hand-coded blocks); `.chat()` uses `create_react_agent` when a chat model exists, else a data-driven tool-router. **Customer agent**: multi-intent routing (`_fallback_route` detects every intent in a message), `create_react_agent` path over 8 tools, greeting/capability handling. **Removed every hardcoded fallback** in `app/agents/`: `142.80`, hash-derived price, `1.8` baseline (orders); the entire MD5-hash stock model + `11115`/`118.5`/`79.99` (inventory) → inventory stock is now `MODELLED` from real `order_items` demand. **Seeded the missing tables**: `order_payments` (3 059), `order_reviews` (2 022), `product_category_name_translation` (71) — billing is now real, product search works in English + Portuguese. Copied the Olist + DataCo CSVs into `backend/data/raw/` (self-contained pipeline). `simulated_clock()` opens its own session + falls back to `max(timestamp)` (fixed "3257 days ago" + fresh-load NOT_ESTIMABLE). **`docs/gap-analysis.md`** written. **51 backend tests green**; `tsc` + `vite build` clean; all 7 dashboard tabs verified against live data. |

### Verified state after this session

- **49 backend tests green** (foundation, auth/RBAC 401/403 enforcement, LLM guardrails, 3 new domain agents + orchestrator contract tests). `tsc` + `vite build` clean.
- `git init` done; `.gitignore` excludes `orders.db`, `node_modules`, `.pytest_cache`, `.env`, `*.backup`; **nothing committed yet** (awaiting user).
- **All 8 agents run**: orders, inventory, customer, logistics, pricing, marketing (6 domain) + orchestrator + the customer multi-agent flow. All 7 dashboard tabs verified in the browser against live data.
- App boots with structured JSON logging + request/correlation ids; auth is soft in dev (`AUTH_ENFORCED=false`) and strict in prod; `/health` + `/health/ready` public; security headers present.
- LLMs: agents answer **without** a key (deterministic, data-grounded, topic-aware). Set `LLM_PROVIDER=openai` + a key (or `OPENAI_API_BASE` at any compatible endpoint, or leave a `GEMINI_WEB2API_*` proxy running) and restart → full conversational answers, same code path.
- `frontend`: eslint/prettier/vitest configs added, `npm ci` needed to install the new devDeps before `npm run lint`.

**Environment note:** this workstation has Docker Desktop installed but the daemon is not running, and Terraform is not installed — so M2/M12 artifacts are authored to spec but `docker compose up` / `terraform apply` must be run by the operator.

---

## 0. Strategy & ground truth

### 0.1 Repos in play

| Repo | Role | Notes |
|---|---|---|
| `C:\Users\A Shrinivas\Downloads\Agents` (**this repo**) | **Canonical product repo**. All work lands here. | Currently: FastAPI + Vite/React, SQLite (`orders.db`, committed), 3 agents (orders, inventory, customer). No auth enforcement, no cloud, no CI, no `requirements.txt`. |
| `..\updated_CommerceOS` (sibling, same author/lineage) | **Port source.** Contains a much more complete implementation of the same platform. | 6 agents + `orchestrator/` + `automation/` + `auth_service`/RBAC/audit + `observability/` + `resilience/` + Redis + Postgres + WebSocket + Next.js + `docker-compose.yml`. Port modules from here rather than writing from scratch, then adapt to this repo's conventions. |
| `sql-agent-main` (reference zip) | **Pattern source** — text-to-SQL analytics agent. | reformulate → classify → CoT plan → generate (few-shot) → execute (AST guardrail + timeout + row caps) → self-correct loop → summarize (+chart). Ships an eval harness: `eval/benchmark.json` (60 graded Q&A) + `eval/eval_app.py`. |
| `langgraph-ecommerce-agent-main` (reference zip) | **Pattern source** — SQL validation, sub-agents, tracing, structured logging, error tests. | `validator(sql)` tool, `sub_agents.py` (segmentation/trends/geo/product), `structlog`, `tests/test_errors.py` + `tests/test_evals.py`, LangSmith tracing. |
| `agentic-ecommerce-main` (reference zip) | **Pattern source** — deployment, memory, telemetry. | `Dockerfile`, `deploy.sh`, `terraform-gcp/` (cloudrun/cloudsql/network modules), LangGraph checkpointing (`langgraph-checkpoint-sqlite`), `memory_node.py` (long-term user profile in `BaseStore`), Langfuse telemetry, `retrieve_catalog` RAG tool. Adapt GCP→AWS. |

### 0.2 Decisions (locked)

1. **Cloud = AWS.** Containers on **ECS Fargate** behind an **ALB**; **RDS PostgreSQL**; **ElastiCache Redis**; **S3 + CloudFront** for the built frontend; **Secrets Manager**; **ECR**; **CloudWatch** logs/metrics/alarms; **WAF** on the ALB/CloudFront. IaC in **Terraform**. CI/CD via **GitHub Actions** → ECR → ECS.
2. **DB = PostgreSQL** everywhere (local via Docker Compose, cloud via RDS). SQLite is removed from the runtime path (kept only as an optional local fallback for unit tests).
3. **LLM = provider-abstracted.** A single `LLMProvider` interface with adapters for **AWS Bedrock** (primary in cloud: Claude / Titan), **OpenAI**, **Anthropic direct**, and the existing deterministic fallback. Selected by env. No agent calls a vendor SDK directly.
4. **Agent set = 6 domain agents + 1 orchestrator + 1 analytics (text-to-SQL) agent.** Domain agents: `orders`, `inventory`, `customer`, `logistics`, `pricing`, `marketing`. Plus `orchestrator` (cross-domain) and `analytics` (NL→SQL over the warehouse).
5. **Auth is enforced** on every non-public route (JWT + RBAC + audit log). The frontend gets a real login screen and route guards.
6. **No mock/hardcoded business logic in the runtime path.** Every number shown to a user is either OBSERVED (DB), CALCULATED (from observed), MODELLED (named algorithm), or explicitly `NOT_ESTIMABLE`. The one known offender (`inventory` stock levels from an MD5 hash) is replaced with a real `inventory` table + stock ledger.

### 0.3 Definition of "production ready" (acceptance for the whole plan)

- [ ] `docker compose up` brings up Postgres + Redis + backend + frontend locally, healthchecks green.
- [ ] `terraform apply` in `infra/aws/envs/prod` provisions the full stack; the app is reachable over HTTPS on a real domain.
- [ ] GitHub Actions: on push to `main`, lint + typecheck + tests + build images + push to ECR + deploy to ECS; on PR, lint + tests only.
- [ ] All 6 domain agents + orchestrator + analytics agent return real, DB-derived output with no `NotImplementedError`, no `TODO`, no hash-derived or constant business values.
- [ ] Every API route (except `/health`, `/api/v1/auth/login`, docs) requires a valid JWT; RBAC enforced; every mutating call writes an audit row.
- [ ] Test suite: backend ≥ 80% line coverage on `app/agents`, `app/orchestrator`, `app/automation`, `app/services`, `app/api`; agent eval harness passes its threshold; frontend component + e2e smoke tests pass.
- [ ] Structured JSON logging with correlation IDs; OpenTelemetry traces exported; CloudWatch dashboards + alarms; `/metrics` Prometheus endpoint.
- [ ] Secrets only in Secrets Manager / GitHub Actions secrets — none in the repo, `.env.example` only.
- [ ] Docs delivered: `docs/architecture.md` (+ rendered diagram), `docs/db-schema.md` (+ ERD), `docs/cost-estimate.md`, `docs/api.md`, `docs/runbook.md`, `docs/security.md`, `README.md` rewritten, and `docs/presentation/` (slide deck).
- [ ] Load test (`k6`) at target RPS meets p95 latency SLO; autoscaling verified.

---

## 1. Gap analysis

Legend: ✅ done · 🟡 partial / needs rework · 🔴 broken or wrong · 🧪 mock/hardcoded/static · ❌ missing

### 1.1 Repository / build / config

| Item | State | Evidence | Gap |
|---|---|---|---|
| `backend/requirements.txt` | ❌ | No file anywhere under `backend/`. Deps happen to be globally installed on the dev machine. | Not reproducible. Need pinned `requirements.txt` (+ `requirements-dev.txt`) or `pyproject.toml`. |
| Python version pin | ❌ | none | Need `.python-version` / `pyproject` `requires-python`. |
| `backend/.env` | 🔴 | Contains only `GEMINI_WEB2API_*` keys that **no code reads**. `llm_service` reads `GEMINI_API_KEY`/`OPENAI_API_KEY`/`GROQ_API_KEY`. So `llm_service.is_available()` is always `False` and **every agent silently runs its deterministic fallback**. | Real settings module + `.env.example`; wire an actual provider. Secret committed to disk → must move to Secrets Manager. |
| `frontend/.env` | 🟡 | Duplicated line (`VITE_API_BASE_URL=...` twice, concatenated). | Clean it; document. |
| `.gitignore` (root) | 🔴 | Empty/absent. `orders.db` (11 MB), `node_modules`, `.pytest_cache`, `backend/.env`, `backend/app/services/llm_service.py.backup` are all committed or committable. | Proper `.gitignore`; purge `orders.db` and `*.backup` from the tree; DB is rebuilt from a pipeline. |
| `Dockerfile` (backend, frontend) | ❌ | none | Need multi-stage Dockerfiles + `.dockerignore`. |
| `docker-compose.yml` | ❌ | none (exists in `updated_CommerceOS`, not here) | Port + adapt. |
| CI/CD | ❌ | no `.github/` | Need GitHub Actions workflows. |
| IaC | ❌ | none | Need `infra/aws/` Terraform. |
| Makefile / task runner | ❌ | none | Add `Makefile`. |
| Lint/format config | ❌ | no `ruff`/`black`/`isort`/`eslint`/`prettier` config in this repo | Add and enforce in CI. |
| `alembic` | 🟡 | `backend/alembic/` exists but the only migration just drops one index; schema is actually created by `Base.metadata.create_all`. `orders.db` is a committed binary. | Real migration chain that builds the full schema from zero on Postgres. |
| Data pipeline | 🟡 | `scripts/seed_nexus_data.py` + `replay_engine.index_events_from_datasets()` read Olist CSVs from `../archive (1)` / `../updated_CommerceOS/...` fallbacks. Only `DataCoSupplyChainDataset.csv` is actually present locally; the Olist CSVs are missing, so a cold rebuild is impossible without them. | Reproducible ingestion: documented dataset acquisition, a `scripts/build_warehouse.py`, checksums, and an idempotent loader that targets Postgres. |

### 1.2 Backend architecture

| Item | State | Evidence | Gap |
|---|---|---|---|
| App layering | 🟡 | `app/main.py`, `app/api/*`, `app/agents/*`, `app/services/*`, `app/models/*`, `app/intelligence/*`. Reasonable, but no `app/core` (settings/logging), no `app/schemas` split, no `app/repositories`, no `app/exceptions`. | Introduce `app/core/` (settings per-env, logging, security), `app/exceptions/`, `app/repositories/` (thin data-access), `app/observability/`. Port shapes from `updated_CommerceOS`. |
| Settings management | 🔴 | Config is `os.environ.get(...)` scattered across `session.py`, `llm_service.py`, `seed_nexus_data.py`, `replay_engine.py`. `DATABASE_URL` defaults to a Windows-path SQLite file. | `pydantic-settings` `Settings` class, env-specific (`development`/`production`), single import site. |
| DB session | 🟡 | `app/database/session.py`: single engine, `check_same_thread=False` SQLite, `run_migrations()` shells out to alembic on startup (fragile), `init_db()` also `create_all`. No pooling config, no health probe, no async. | Postgres engine w/ pool sizing + `pool_pre_ping`; drop the subprocess alembic call (run migrations as an explicit deploy step / entrypoint); `get_db` unchanged; add `check_db()` for `/health`. |
| Error handling | 🔴 | No exception handlers registered on the app. Agents catch broadly and return partial dicts; routes let 500s leak stack traces (CORS `allow_origins=["*"]`). | Central exception handlers (`app/exceptions/`), typed exceptions, RFC-7807 problem responses, no stack traces in prod, request-id echo. |
| Logging | 🔴 | `logging.basicConfig` text logs. No correlation IDs, no request logging middleware, no structured output. `agent.log`/`logs/` not gitignored. | `structlog` JSON logging, `X-Request-ID` middleware, correlation-id contextvar, per-agent execution-id in every log line. |
| CORS / security headers | 🔴 | `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` — invalid combo and unsafe. No `TrustedHostMiddleware`, no security headers, no rate limiting, no body-size limit. | Env-driven allowlist; `TrustedHostMiddleware`; security headers middleware; rate limiting (Redis token bucket); `--limit-max-requests` / body cap. |
| AuthN/AuthZ | 🔴🧪 | `app/models/security.py` defines `User`, `UserRole`, `Notification`, `AgentPrediction`. **No `auth_service`, no login route, no `get_current_user` dependency, no route is protected.** `InventoryAgentView.tsx` sends `Authorization: Bearer <token>` if `localStorage` has one, but nothing issues or checks tokens. | Port `auth_service` + `api/v1/auth.py` + `api/v1/deps.py` + `api/v1/audit.py` + `api/v1/users.py` from `updated_CommerceOS`; hash with bcrypt/argon2; JWT via `python-jose`; seed users; protect **all** routers; audit every mutation. |
| Realtime | 🔴 | Frontend polls (`setInterval` 1.5 s) `/api/orders/ingestion/status`. No WebSocket/SSE for agent runs or notifications. `replay_engine` runs an in-process `asyncio` task — lost on restart, not shared across workers/instances. | Redis pub/sub event bus + `/api/v1/stream/ws` (port from `updated_CommerceOS`); replay engine state in Redis so it survives restarts and is single-writer across the cluster. |
| Background work | 🔴 | `replay_engine._run_loop` is an in-process task started from an HTTP handler; multiple Uvicorn workers ⇒ multiple loops ⇒ double ingestion. `notification_service` writes synchronously in the request path. | Move replay + periodic agent runs to a dedicated worker process/service (ECS service or `arq`/Celery on Redis); leader-election or single-replica for the replay writer. |
| `app/api/routes.py` ingestion control | 🟡 | `POST /api/orders/ingestion/control` mutates global replay state with **no auth**, `clear_db=True` by default (drops all order tables). `reset` wipes data. | Guard behind `SUPER_ADMIN`; make destructive ops explicit + audited; move under `/api/v1/simulation/*`. |
| API versioning & consistency | 🟡 | Mixed prefixes: `/api/orders/*`, `/api/v1/agents/inventory/*`, `/api/customer/*`, `/api/simulation/*`, `/dashboard/simulation/*`. Inconsistent request/response envelopes. | Consolidate under `/api/v1/*`. One response envelope. Keep old paths as 301/aliases for one release. |
| OpenAPI / docs | 🟡 | Default `/docs` on, unauthenticated, in prod. Tags inconsistent. | Gate `/docs` in prod (or keep but behind auth); curated tags + descriptions; export `openapi.json` in CI for `docs/api.md`. |

### 1.3 Agents

| Agent | State | Evidence | Gap |
|---|---|---|---|
| `orders` | 🟡 | `app/agents/orders/` — full: `agent.py` (9-phase lifecycle), `data_layer.py`, `graph.py` (LangGraph triage→tools→response w/ retry), `tools.py` (18 tools), `schemas.py`. Empirical stats, confidence, anomaly, forecast. **But**: LLM path never runs (see 1.1 `.env`); `lookup_order` opens its own `SessionLocal()` ignoring the request session; some fallback constants (`total = 142.80`, `1.8` baseline) in `agent.py`/`tools.py`; no tests for the graph; notifications written in-request. | Wire LLM; thread the request session through tools; remove numeric fallbacks or label them `ESTIMATED`; graph unit tests; move notification writes to events. |
| `inventory` | 🔴🧪 | `app/agents/inventory/tools.py::_deterministic_stock()` derives "stock on hand" from `md5(product_id)` — **fabricated inventory**. `get_inventory_metrics` falls back to `total_p = 11115` and `avg_price = 118.5` constants. Reorder point / EOQ math is real but fed by fake stock + fake daily sales (`hashlib...%25`). | Create a real `inventory` + `stock_movements` schema; seed opening balances from order history (units shipped) + a synthetic-but-persisted replenishment model; ROP/EOQ then compute from real rows. Remove every hash/constant. |
| `customer` | 🟡 | `app/agents/customer/` (added this session) — triage→router→specialists→supervisor over the shared DB. Works deterministically. **But**: LLM path never runs; no persistence of conversations; no long-term customer memory; specialist "billing" reads `OrderPayment` which is **not seeded** (0 rows) so it always falls to `order_total`; no tests beyond the happy path; SSE endpoint re-runs the pipeline rather than streaming tokens. | Seed `order_payments`; add conversation + memory tables (adapt `agentic-ecommerce` `memory_node`); wire LLM streaming; expand tests; add CSAT / review-sentiment tool over `order_reviews`. |
| `logistics` | ❌ | Not in this repo. Exists in `updated_CommerceOS/backend/app/agents/logistics/` (agent/data_layer/graph/schemas/tools). | Port + adapt: carrier performance, lane transit-time distributions, late-delivery risk, SLA breach detection, route/hub bottleneck findings, delivery ETA model. Uses DataCo `days_for_shipping_*`, `shipping_mode`, `order_region`, Olist carrier/customer dates. |
| `pricing` | ❌ | Not here. Exists in `updated_CommerceOS/backend/app/agents/pricing/`. | Port + adapt: margin analysis (DataCo `order_item_profit_ratio`, `product_price`, discount), price-elasticity estimate, discount-leakage detection, competitor-gap placeholder → real category benchmark, markdown recommendations. |
| `marketing` | ❌ | Not here. Exists in `updated_CommerceOS/backend/app/agents/marketing/`. | Port + adapt: campaign/traffic proxy from order velocity, category demand trends, RFM customer segmentation (`order_reviews`, `customers`, `order_items`), promo-response, CAC/LTV proxy, next-best-campaign. |
| `analytics` (text-to-SQL) | ❌ | Not present in any form here. `sql-agent` reference implements it fully. | New `app/agents/analytics/` — NL→SQL over the read-replica warehouse with the `sql-agent` node graph (reformulate/classify/plan/generate/execute/self-correct/summarize), the `langgraph-ecommerce` `validator`, AST guardrail + statement timeout + row caps, read-only DB role, `eval/benchmark.json`. Powers a "Ask the data" panel + feeds other agents. |
| `orchestrator` | ❌ | Not here. `updated_CommerceOS/backend/app/orchestrator/` has `coordinator.py`, `router.py`, `correlation.py`, `conflict_resolution.py`, `execution_context.py`. | Port: event → route to relevant agents → collect findings/risks → cross-domain correlation → conflict resolution → coordinated execution plan → persist as operational events + notifications. Add a `/api/v1/orchestrator/run` + scheduled runs. |
| `automation` / approvals | ❌ | Not here. `updated_CommerceOS/backend/app/automation/` has `actions.py`, `approval.py`, `executor.py`, `policies.py`, `rollback.py`, `verification.py`. Current agents produce `automation_eligibility` metadata but nothing consumes it. | Port: policy engine (which actions may auto-execute vs need approval by which role), executor with rollback, verification pass, human-in-the-loop approval queue + UI. |
| LangGraph checkpointing / memory | ❌ | Graphs are compiled with no checkpointer; each `invoke` is stateless; no thread persistence, no long-term store. | Add `langgraph-checkpoint-postgres` (or Redis) saver; per-conversation `thread_id`; `BaseStore` for long-term memory (customer profiles, agent learnings). Pattern: `agentic-ecommerce`. |
| Agent eval / quality | ❌ | No eval harness, no golden set, no regression gate. `updated_CommerceOS` has some; `sql-agent` has `benchmark.json` + `eval_app.py`. | `eval/` package: golden Q&A per agent, scorers (exact/【F1】/LLM-judge), latency + token budget, CI gate, HTML report. |
| LLM safety | 🔴 | No prompt-injection defense, no output validation, no PII redaction, no tool-call allow-listing beyond intent switch, no cost/token ceiling, no timeout on LLM calls in `llm_service` beyond `urllib` 10–15 s. | Input sanitization, structured-output validation (pydantic), per-request token/cost budget, provider timeouts + retries + circuit breaker, PII scrub on logs, refusal handling. |

### 1.4 Database & data

| Item | State | Evidence | Gap |
|---|---|---|---|
| Engine | 🔴 | SQLite file `orders.db` committed (11 MB); `DATABASE_URL` default is a `sqlite:///<win path>`. | PostgreSQL. Migrations build schema. Seed/replay target Postgres. |
| Schema completeness | 🟡 | `models/olist.py` (Customer, Seller, Product, Order, OrderItem, OrderPayment, OrderReview, Geolocation, CategoryTranslation, Inventory) + `models/dataco.py` (DataCoOrder, DataCoOrderItem) + `models/security.py` (User, Notification, AgentPrediction). | Missing: `stock_movements`, `agent_runs`, `agent_findings`, `automation_actions`, `approvals`, `audit_log`, `conversations`, `conversation_messages`, `customer_memory`, `orchestration_decisions`, `campaigns`, `price_experiments`, `carrier_lanes`. Add. |
| Seeded data gaps | 🧪 | `order_payments` and `order_reviews` are **not** seeded by `seed_nexus_data.py` / `replay_engine` (only orders/items/customers/products + DataCo orders). Billing + sentiment features have no data. `Inventory` table never populated. | Extend loader to ingest all Olist CSVs; populate `inventory`/`stock_movements` from a documented model. |
| Indices & constraints | 🟡 | Some indices on models. FKs declared. No partial indexes for hot query paths (pending statuses, date-range scans), no `CHECK`s, no `NOT NULL` on business-critical cols in DataCo. | Add covering/partial indexes for the agent query patterns; add constraints; `EXPLAIN ANALYZE` the top 10 agent queries. |
| Read/write split | ❌ | Single connection. Analytics agent will run arbitrary SQL. | RDS read replica; analytics agent + heavy dashboards use a **read-only** role on the replica. |
| Migrations tooling | 🟡 | Alembic present but unused for real. | Full baseline migration + per-feature migrations; `alembic upgrade head` in the deploy entrypoint (not app startup). |
| Data retention / PII | ❌ | Olist data is anonymized, but `users`, `audit_log`, `conversations` will hold real PII once deployed. No retention, no encryption-at-rest config called out, no backup policy. | Document PII inventory; RDS encryption + automated backups + PITR; retention jobs; `docs/security.md`. |

### 1.5 Frontend

| Item | State | Evidence | Gap |
|---|---|---|---|
| Framework | 🟡 | Vite + React 18 + TS, **no router**, one `App.tsx` with a 3-way tab `useState`. Inline styles everywhere, no design system, no component lib. `OrdersAgentDashboard.tsx` is 1587 lines. | Add `react-router`; split routes/pages; adopt a styling system (Tailwind or CSS modules) consistently; break up god components; shared `api/` client with auth + error handling; `@tanstack/react-query` for data fetching/caching instead of hand-rolled `useEffect`+`setInterval`. |
| Auth UI | ❌ | No login page, no route guard, no token storage strategy (just ad-hoc `localStorage.getItem` reads). | Login page, auth context, protected routes, token refresh, logout, 401 → redirect. |
| Realtime | 🔴 | `setInterval` polling every 1.5 s while ingestion runs; every dashboard re-fetches on a `refreshKey` bump. | WebSocket client; subscribe to agent-run + notification streams; optimistic + push updates. |
| Error/empty/loading states | 🟡 | Some. No error boundary, no global toast, no retry UX in most places, no skeletons. | `ErrorBoundary`, toast system, consistent skeletons, offline banner. |
| Config | 🔴 | `config.ts` hardcodes `http://localhost:8000`; `import.meta.env` fixed via `vite-env.d.ts` this session. | Build-time env for API base + WS base + auth; documented; injected at deploy for S3/CloudFront. |
| Accessibility | 🔴 | Color-only status encoding, no focus management, no aria on interactive divs, tab nav is `<button>`s but panels aren't `role=tabpanel`. | a11y pass: semantic landmarks, aria, keyboard nav, contrast, reduced-motion. |
| Tests | ❌ | none | Vitest + React Testing Library component tests; Playwright e2e smoke (login → each dashboard → run an agent → see result). |
| Build output / hosting | 🟡 | `vite build` works. No hosting story. | S3 + CloudFront w/ cache-busting + SPA fallback; `Dockerfile` alt for ECS-served static if needed. |
| Dashboards for new agents | ❌ | Only orders/inventory/customer. | Add Logistics, Pricing, Marketing, Analytics ("Ask the data"), Orchestrator (cross-domain command center), Approvals (HITL queue), Notifications center, Admin (users/audit). |
| Screenshot/visual polish | 🟡 | Dark theme, decent. Sticky-header + tall-page compositing quirk observed (cosmetic). | Design pass once routing/design-system lands. |

### 1.6 Intelligence / ML

| Item | State | Evidence | Gap |
|---|---|---|---|
| Stats primitives | ✅ | `app/intelligence/statistics/profiler.py` (percentiles, IQR, MAD, skew, Tukey fences), `confidence/calculator.py`, `anomaly/detector.py` (modified z / MAD), `forecasting/engine.py` (OLS + intervals). Pure, no hardcoded thresholds. Good. | Add: Holt-Winters / seasonal-naive to `forecasting`; STL decomposition; `recommendations/` (missing here, exists in `updated_CommerceOS`); `explainability/`; `risk/` scoring module. Unit tests for each. |
| Forecast validation | ❌ | No backtest, no MAPE/RMSE reporting. | Backtesting harness; report accuracy per metric in the eval report. |
| Feature reproducibility | 🟡 | Features computed ad-hoc in each `data_layer.py`. | A `features/` module or SQL views for shared definitions (pending-age, delay-margin, RFM) so agents agree. |

### 1.7 Security

| Item | State | Gap |
|---|---|---|
| AuthN/Z | 🔴 none enforced | JWT + RBAC + audit (port from `updated_CommerceOS`). |
| Secrets | 🔴 `backend/.env` committed | Secrets Manager; rotate; scrub git history. |
| Transport | 🔴 http only | ACM cert, HTTPS-only, HSTS, redirect. |
| Input validation | 🟡 pydantic on some bodies | Validate + bound every input; reject oversize; sanitize NL going to LLM. |
| SQL injection (analytics agent) | 🔴 (will be) | Read-only DB role + AST allow-list (SELECT only) + statement timeout + row cap + no multi-statement. |
| Dependency scanning | ❌ | `pip-audit` / `npm audit` / Dependabot / Trivy image scan in CI. |
| Rate limiting / abuse | ❌ | Redis token bucket per IP + per user; WAF rules; login lockout. |
| Least privilege (cloud) | ❌ | Per-service IAM roles; no `*` policies; SGs least-open; private subnets for RDS/Redis. |
| Audit | 🔴 model absent here | `audit_log` table + middleware; immutable; shipped to CloudWatch. |
| PII | 🔴 | Inventory + redaction + retention (`docs/security.md`). |

### 1.8 Observability

| Item | State | Gap |
|---|---|---|
| Logging | 🔴 text, no correlation | structlog JSON + request-id + execution-id. |
| Metrics | ❌ | `prometheus-fastapi-instrumentator` `/metrics`; agent run counters, latency histograms, LLM token/cost counters, error rates. CloudWatch EMF or ADOT → CloudWatch/AMP. |
| Tracing | ❌ | OpenTelemetry SDK; instrument FastAPI + SQLAlchemy + LLM calls; export to AWS X-Ray (ADOT) or Langfuse. Pattern: `agentic-ecommerce`. |
| Dashboards / alarms | ❌ | CloudWatch dashboard (RPS, p95, 5xx, agent failures, replay lag, DB conns, Redis mem); alarms → SNS. |
| Healthchecks | 🟡 `/` and `/api/orders/health` (agent health, not infra) | `/health` (liveness), `/health/ready` (DB+Redis+LLM reachable), used by ALB + ECS + compose. |
| Cost visibility | ❌ | LLM token/$ metric per agent per day; budget alarm. |

### 1.9 Testing

| Item | State | Evidence | Gap |
|---|---|---|---|
| Backend unit/integration | 🟡 | `tests/test_orders_agent.py` (13), `tests/test_inventory_agent.py` (3), `tests/test_customer_agent.py` (7). 23 pass. All hit the live committed `orders.db`. No isolation, no fixtures factory, no coverage gate, no graph tests, no error-path tests, no auth tests. | pytest + `pytest-cov` gate; ephemeral Postgres (testcontainers) or transactional fixtures; factory-boy; graph/branch tests; error-injection tests (pattern: `langgraph-ecommerce/tests/test_errors.py`); auth/RBAC tests; contract tests for every route. |
| Agent evals | ❌ | none | `eval/` golden sets + CI gate. |
| Frontend | ❌ | none | Vitest + RTL + Playwright. |
| Load/perf | ❌ | none | `k6` scripts + SLO. |
| Contract/OpenAPI | ❌ | none | Schemathesis against `openapi.json` in CI. |

### 1.10 Documentation & deliverables

| Item | State | Gap |
|---|---|---|
| `README.md` | 🟡 describes only the Orders agent, local run | Full rewrite: overview, architecture, all agents, local dev, deploy, env vars, troubleshooting. |
| Architecture doc | ❌ | `docs/architecture.md` + C4 / mermaid diagrams + sequence diagrams for orchestrator + agent run. |
| DB schema doc | ❌ | `docs/db-schema.md` + generated ERD (`eralchemy` / `schemacrawler`) + table dictionary. |
| Cost estimate | ❌ | `docs/cost-estimate.md` — AWS monthly at 3 load levels + LLM token cost model + a spreadsheet. |
| API doc | ❌ | `docs/api.md` from `openapi.json` (redoc-cli) + auth guide. |
| Runbook / ops | ❌ | `docs/runbook.md` — deploy, rollback, incident, backup/restore, scaling, on-call. |
| Security doc | ❌ | `docs/security.md` — threat model, PII, RBAC matrix, secret handling, compliance notes. |
| Presentation | ❌ | `docs/presentation/` — reveal.js deck (or `.pptx` via `python-pptx`): problem, solution, architecture, demo, agents, results, cost, roadmap. |
| ADRs | ❌ | `docs/adr/` — record the locked decisions in 0.2. |

---

## 2. Target architecture (AWS)

```
Route53 ──> CloudFront (WAF) ──> S3 (React SPA build)
                     │
                     └─(/api/*, /ws)─> ALB (WAF, ACM TLS) ──> ECS Fargate service: api  (2+ tasks, autoscale)
                                                          └─> ECS Fargate service: worker (replay + schedules + orchestrator cron, 1 leader)
                                                                    │
                        ┌───────────────────────────────────────────┼───────────────────────────────┐
                        ▼                                           ▼                               ▼
              RDS PostgreSQL (Multi-AZ)                   ElastiCache Redis                  Amazon Bedrock
              + read replica (analytics)                  (event bus, cache,                 (LLM: Claude / Titan)
                        │                                  rate-limit, replay state)          + OpenAI/Anthropic fallback
                        ▼
              S3 (dataset lake, backups, eval reports)     Secrets Manager (DB creds, JWT key, LLM keys)
              CloudWatch (logs, metrics, alarms) + X-Ray (ADOT)   SNS (alerts)
```

- **Networking:** 1 VPC, 2 AZs, public subnets (ALB, NAT), private subnets (ECS, RDS, Redis). RDS/Redis not publicly routable.
- **Images:** ECR repos `commerceos/api`, `commerceos/frontend` (if ECS-served) — built in CI, scanned by Trivy + ECR scan.
- **Config:** ECS task defs pull secrets from Secrets Manager; non-secret config via SSM Parameter Store.
- **Scaling:** api service target-tracking on CPU + ALB RPS; worker fixed at 1 (single replay writer) with a Redis lock as belt-and-braces.
- **Envs:** `dev` (single-AZ, smaller instances, on/off), `prod` (Multi-AZ). Terraform workspaces or `envs/{dev,prod}`.

---

## 3. Work breakdown

Each task: **[ID] Title — priority (P0 blocker … P3 nice) — files — depends on — acceptance.**

### M1 — Foundation, config, local dev (P0)

- **[F1] Dependency manifests** — `backend/requirements.txt`, `backend/requirements-dev.txt`, `backend/pyproject.toml` (ruff+black+isort+mypy config), `.python-version`. Pin every package (freeze current working set: fastapi 0.141, langgraph 1.2, langchain 1.4, sqlalchemy 2.0, pydantic 2.13, openai 3.11, redis 8.1, psycopg2-binary 2.9, python-jose, passlib, bcrypt, structlog, pandas; add `alembic`, `pydantic-settings`, `prometheus-fastapi-instrumentator`, `opentelemetry-*`, `boto3`, `sqlparse`, `tenacity`, `arq`). — deps: none — **accept:** fresh venv `pip install -r` runs the app + tests.
- **[F2] Settings module** — `backend/app/core/settings/{__init__.py,base.py,development.py,production.py}` using `pydantic-settings`. Fields: `ENVIRONMENT`, `DATABASE_URL`, `DATABASE_READ_URL`, `REDIS_URL`, `JWT_SECRET`, `JWT_TTL_MIN`, `LLM_PROVIDER`, `BEDROCK_REGION`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`, `CORS_ORIGINS`, `LOG_LEVEL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `RATE_LIMIT_*`, `SEED_ADMIN_PASSWORD`. Delete scattered `os.environ` reads in `session.py`, `llm_service.py`, `seed_nexus_data.py`, `replay_engine.py` → import `settings`. — deps: F1 — **accept:** `python -c "from app.core.settings import settings; print(settings.ENVIRONMENT)"`; no `os.environ.get` outside `core/`.
- **[F3] Logging** — `backend/app/core/logging.py` (structlog JSON, level from settings), `backend/app/api/middleware/request_context.py` (`X-Request-ID` in/out, correlation-id contextvar). Wire in `main.py`. Remove `logging.basicConfig`. — deps: F2 — **accept:** every log line is JSON with `request_id`; `agent.log` gitignored.
- **[F4] Exceptions** — `backend/app/exceptions/base.py` (`AppException`, `AuthenticationException`, `AuthorizationException`, `NotFoundException`, `ValidationException`, `LLMException`, `AgentExecutionException`), `backend/app/api/error_handlers.py` (RFC-7807, no stack traces when `ENVIRONMENT=production`). Register in `main.py`. — deps: F2 — **accept:** raising each type yields the right status + shape; 500s show a generic body in prod mode.
- **[F5] `.gitignore` + secret hygiene** — root `.gitignore` (`__pycache__`, `*.pyc`, `.pytest_cache`, `node_modules`, `dist`, `.env`, `*.env` except `.env.example`, `*.db`, `*.backup`, `.terraform`, `*.tfstate*`, `logs/`, `.coverage`, `htmlcov/`). Delete `backend/app/services/llm_service.py.backup`, `backend/.env` (replace with `backend/.env.example`), untrack `orders.db` (DB is rebuilt). Add `frontend/.gitignore`. — deps: none — **accept:** `git status` clean of the above; `.env.example` documents every var from F2.
- **[F6] Docker** — `backend/Dockerfile` (multi-stage: builder installs wheels, runtime slim, non-root, `HEALTHCHECK`, gunicorn+uvicorn workers), `backend/.dockerignore`, `frontend/Dockerfile` (node build → nginx serve) + `frontend/nginx.conf` (SPA fallback, gzip, security headers), `frontend/.dockerignore`. — deps: F1 — **accept:** both images build; `docker run` backend serves `/health`.
- **[F7] docker-compose** — root `docker-compose.yml` (postgres:15, redis:7, backend, worker, frontend) + `docker-compose.override.yml` for dev volumes/hot-reload. Port from `updated_CommerceOS`, adapt names/ports. Add `.env.example` at root for compose. — deps: F6, D1 — **accept:** `docker compose up` → all healthchecks green, frontend loads, login works.
- **[F8] Makefile** — targets: `setup`, `dev`, `up`, `down`, `logs`, `migrate`, `seed`, `test`, `test-cov`, `lint`, `fmt`, `typecheck`, `eval`, `build`, `deploy-dev`, `deploy-prod`. — deps: F1 — **accept:** `make test` runs full suite.
- **[F9] Pre-commit** — `.pre-commit-config.yaml` (ruff, black, isort, mypy, prettier, eslint, detect-secrets, end-of-file, trailing-whitespace). — deps: F1 — **accept:** `pre-commit run --all-files` passes after F-series.

### M2 — Database & data pipeline (P0)

- **[D1] Postgres migration** — `backend/alembic/versions/0001_baseline.py`: create the **entire** current schema (all `models/olist.py`, `models/dataco.py`, `models/security.py` tables) for Postgres from zero. Fix `alembic/env.py` to import `app.models.Base` and read `settings.DATABASE_URL`. Remove `run_migrations()` subprocess call from `session.py`; migrations run via `alembic upgrade head` in the container entrypoint / a one-shot ECS task. — deps: F2 — **accept:** against empty Postgres, `alembic upgrade head` builds all tables; `alembic downgrade base` drops them.
- **[D2] New tables migration** — `0002_platform_tables.py` + new model files: `stock_movements`, `agent_runs`, `agent_findings`, `automation_actions`, `approvals`, `audit_log`, `conversations`, `conversation_messages`, `customer_memory`, `orchestration_decisions`, `campaigns`, `price_experiments`, `carrier_lanes`. Models under `backend/app/models/{operations.py,agents.py,commerce.py}`. — deps: D1 — **accept:** migration up/down clean; models importable; FKs valid.
- **[D3] Session/engine rework** — `backend/app/database/session.py`: Postgres engine (`pool_size`, `max_overflow`, `pool_pre_ping`, `pool_recycle`), `SessionLocal`, `ReadSessionLocal` (bound to `DATABASE_READ_URL`, autocommit read-only), `get_db`, `get_read_db`, `check_db()` (SELECT 1 both). — deps: F2, D1 — **accept:** `check_db()` true against compose Postgres; write via `get_db`, read via `get_read_db`.
- **[D4] Warehouse build script** — `backend/scripts/build_warehouse.py`: idempotent loader for **all** Olist CSVs (orders, order_items, customers, products, sellers, geolocation, **order_payments**, **order_reviews**, category_translation) + DataCo. Config: `DATASET_DIR` env. Emits row counts + a manifest with SHA-256 of each source. Targets Postgres via `SessionLocal`, `ON CONFLICT DO NOTHING`. — deps: D1 — **accept:** on empty DB, one run loads all tables; second run is a no-op; `order_payments` and `order_reviews` non-empty.
- **[D5] Inventory seeding (replace mock)** — `backend/scripts/seed_inventory.py` + logic in a new `app/agents/inventory/replenishment.py`: compute opening stock per product = f(units sold in history, category turnover) and write `inventory` rows + an initial `stock_movements` ledger; the replay engine then decrements `inventory` on each shipped order and appends `stock_movements`. Delete `_deterministic_stock()` and all `11115`/`118.5` constants from `inventory/tools.py` + `inventory/agent.py`. — deps: D2, D4 — **accept:** `inventory` has one row per product with a plausible distribution; `InventoryTools.query_products` reads real rows; grep shows no `hashlib`/`md5`/`11115` in `app/agents/inventory/`.
- **[D6] Replay engine → Postgres + Redis** — `backend/app/services/replay_engine.py`: (a) all writes target Postgres batch inserts (already close); (b) engine **state** (`status`, `speed`, `_current_index`, `current_simulated_date`, `events_processed`) persisted in Redis so it survives restarts and is authoritative cluster-wide; (c) a Redis lock (`SET NX`) so only the `worker` service runs `_run_loop`; the `api` service only reads status + issues control commands via Redis. Index cache (`_events_index`) built once by the worker from S3/local CSVs. — deps: D3, R1 — **accept:** restart the worker mid-run → resumes from Redis position; start a 2nd worker → it does not double-ingest.
- **[D7] Query performance** — `0003_indexes.py`: partial indexes for pending statuses, `(order_purchase_timestamp)` range, `(order_status, order_purchase_timestamp)`, DataCo `(order_date)`, `order_items(product_id)`, `order_reviews(order_id)`, FKs. Add a `scripts/explain_agent_queries.py` that runs `EXPLAIN (ANALYZE, BUFFERS)` on the top queries from each `data_layer.py`. — deps: D4 — **accept:** each top query < 100 ms on the full dataset; no seq scan on the big tables for the hot paths.
- **[D8] ERD + schema doc generation** — `scripts/gen_erd.py` (eralchemy2 or schemacrawler) → `docs/db-schema.md` + `docs/img/erd.svg`. — deps: D2 — **accept:** ERD renders; every table has a description row.

### M3 — Security & auth (P0)

- **[S1] Auth service** — port `backend/app/services/auth_service.py` from `updated_CommerceOS` (bcrypt/argon2 hashing, `create_access_token`/`decode_access_token` via `python-jose`, `authenticate_user`, `get_user_by_*`, `log_audit`). Adapt imports to this repo's `settings`, `models.security`. — deps: F2, D2 — **accept:** unit tests: hash/verify round-trip, token encode/decode, expiry, tamper rejection.
- **[S2] Auth API + deps** — port `backend/app/api/v1/auth.py` (`POST /login`, `GET /me`, `POST /logout`, refresh), `backend/app/api/v1/deps.py` (`get_current_user`, `get_optional_user`, `require_super_admin`, `require_agent_access(agent)`, `require_role(*roles)`). Remove the "synthesize a user if token has no match" shortcut present in the source (it's a security hole) — unknown subject ⇒ 401. — deps: S1 — **accept:** `/me` returns the user for a valid token, 401 otherwise; RBAC dep blocks wrong-role.
- **[S3] Protect every router** — add `dependencies=[Depends(get_current_user)]` (or stricter RBAC) to **all** routers in `main.py` except `/health*`, `/api/v1/auth/login`, and (dev only) `/docs`. Simulation/ingestion control ⇒ `require_super_admin`. Agent query endpoints ⇒ `require_agent_access(<agent>)` or any authenticated user for read. — deps: S2 — **accept:** contract test: every route in `openapi.json` returns 401 without a token (allow-list the 3 public ones).
- **[S4] Audit middleware** — `backend/app/api/middleware/audit.py`: for every non-GET 2xx/4xx response to an authenticated route, write an `audit_log` row (actor, action=`METHOD path`, entity, ip, request_id, status, latency). Also explicit `log_audit` calls in auth + automation. — deps: D2, S1 — **accept:** a POST leaves exactly one audit row; audit rows are append-only (no update/delete route).
- **[S5] User seeding** — `backend/scripts/seed_users.py`: one user per `UserRole` with password from `settings.SEED_ADMIN_PASSWORD` (dev) / generated + stored in Secrets Manager (prod, one-shot). — deps: S1, D2 — **accept:** `make seed` creates 7 users; login works for each.
- **[S6] CORS / hosts / headers / limits** — `main.py`: `CORSMiddleware` from `settings.CORS_ORIGINS` (no `*` with credentials); `TrustedHostMiddleware`; `backend/app/api/middleware/security_headers.py` (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, minimal CSP for `/docs`); body-size limit; `--limit-concurrency`. — deps: F2 — **accept:** headers present; wildcard origin gone; 413 on oversize body.
- **[S7] Rate limiting** — `backend/app/api/middleware/rate_limit.py`: Redis token-bucket, per-IP global + stricter per-route for `/auth/login` (lockout after N fails) and LLM-backed agent endpoints. — deps: R1 — **accept:** exceeding the bucket returns 429 with `Retry-After`; login lockout works.
- **[S8] Analytics agent SQL safety** — in `app/agents/analytics/`: dedicated **read-only** Postgres role (`commerceos_ro`, `GRANT SELECT` only) via migration; executor uses `DATABASE_READ_URL` with that role; `sqlparse` AST guard (single statement, `SELECT`/`WITH` only, no `pg_`/`information_schema` writes), `statement_timeout=5s`, `LIMIT` injection / `fetchmany` cap, deny comments-only. Pattern from `sql-agent/agent.py::execute_sql`. — deps: D3, A8 — **accept:** `DROP`, `INSERT`, `; DELETE`, `pg_read_file` all rejected before execution; a 10 M-row cross join times out, not OOMs.
- **[S9] Secrets** — remove `backend/.env` from git (F5); `infra` provisions Secrets Manager entries; ECS task defs reference them; local uses `.env` (gitignored). `detect-secrets` baseline in pre-commit. Document rotation in `docs/security.md`. Scrub `backend/.env`'s current value from git history (`git filter-repo`) — note in runbook. — deps: F5, I-series — **accept:** no secret string anywhere in the tree; CI secret-scan passes.
- **[S10] LLM guardrails** — in `app/services/llm/` (see A1): per-call timeout + `tenacity` retry + circuit breaker; per-request token & USD budget (config); pydantic validation of every structured output; strip/deny prompt-injection markers on untrusted input; PII scrub before logging prompts. — deps: A1 — **accept:** a hung provider trips the breaker < timeout; over-budget request is refused with a clear error; malformed LLM JSON raises `LLMException`, not a 500.

### M4 — Realtime & background (P1)

- **[R1] Redis client + event bus** — `backend/app/core/redis.py` (pooled client), `backend/app/services/event_bus.py` reworked: keep the in-proc `EventBus` for same-process listeners **and** publish to Redis `commerceos:events`; a subscriber in the api process fans out to WebSockets. Port shape from `updated_CommerceOS/backend/app/events/`. — deps: F2 — **accept:** publish in worker → api WS clients receive it.
- **[R2] WebSocket endpoint** — `backend/app/api/v1/stream.py`: `GET /api/v1/stream/ws?token=` (JWT in query or header), channels: `agent_runs`, `notifications`, `ingestion`, `orchestrator`. Heartbeat, auth, per-user filtering by role. — deps: R1, S2 — **accept:** authenticated client receives ingestion ticks + a notification when an agent run creates one; unauth ⇒ closed.
- **[R3] Worker service** — `backend/app/worker/main.py` (`arq` worker or a plain asyncio loop): owns the replay `_run_loop`, runs scheduled agent analyses (cron: each domain agent every N min), runs the orchestrator on a schedule + on demand, prunes old `agent_runs`/notifications. Separate container in compose + ECS. — deps: D6, O1 — **accept:** `docker compose up` shows the worker ingesting + periodic agent runs appearing in `agent_runs`.
- **[R4] Frontend realtime client** — `frontend/src/lib/ws.ts` + `frontend/src/hooks/useRealtime.ts`; replace `setInterval` polling in `App.tsx` / dashboards with WS subscriptions + `react-query` cache updates. — deps: R2, FE1 — **accept:** ingestion bar + notifications update with no polling; Network tab shows one WS, not a poll loop.

### M5 — LLM provider & agent hardening (P1)

- **[A1] LLM provider abstraction** — `backend/app/services/llm/{__init__.py,base.py,bedrock.py,openai.py,anthropic.py,deterministic.py,factory.py}`. `LLMProvider` interface: `generate_text`, `generate_json(schema)`, `stream_text`, `embed` (for RAG). `factory` picks by `settings.LLM_PROVIDER`. Replace `app/services/llm_service.py` (keep a shim that delegates, then delete once agents migrate). — deps: F2, S10 — **accept:** with `LLM_PROVIDER=deterministic` all agents behave as today; with `openai`/`bedrock` + a key, `generate_json` returns validated structured output; provider errors are typed.
- **[A2] Migrate agents to the abstraction** — `orders/graph.py`, `inventory/graph.py`, `customer/graph.py` `llm_service` → `llm` provider; remove numeric fallbacks flagged in 1.3 (`142.80`, `1.8`, `118.5`, `11115`) or relabel as `ESTIMATED` with a reason. — deps: A1, D5 — **accept:** grep for the constants is clean; each agent works in both `deterministic` and a real-provider mode; existing tests pass.
- **[A3] LangGraph checkpointing + memory** — add `langgraph-checkpoint-postgres` saver to all compiled graphs; `thread_id` per conversation/run; `BaseStore` (Postgres) for long-term memory. `customer` agent: write/read `customer_memory` (adapt `agentic-ecommerce/memory_node.py` + `user_profile.py`). — deps: D2, A1 — **accept:** a 2-turn customer conversation resolves a pronoun from turn 1; killing the process mid-graph and re-invoking with the same `thread_id` resumes.
- **[A4] Logistics agent** — port `app/agents/logistics/` from `updated_CommerceOS`; adapt `data_layer.py` to this repo's models + simulated clock; findings: lane transit-time distribution + SLA breach, carrier late-rate outliers, hub bottleneck, ETA model (`forecasting`), route recommendations. Route `app/api/v1/agents/logistics.py`. Dashboard FE7. — deps: A1, D3 — **accept:** `POST /api/v1/agents/logistics/analyze` returns findings with evidence + confidence on the real dataset; unit + eval tests.
- **[A5] Pricing agent** — port `app/agents/pricing/`; findings: category margin distribution + low-margin outliers, discount leakage (`order_item_discount_rate` vs profit), elasticity estimate (price vs units by category over time), markdown/again-price recommendations with expected margin delta. — deps: A1, D3 — **accept:** analyze endpoint returns margin findings + a priced recommendation; tests.
- **[A6] Marketing agent** — port `app/agents/marketing/`; RFM segmentation over `customers`+`order_items`+`order_reviews`; category demand trend + seasonality (`forecasting` STL); promo-response proxy; next-best-campaign per segment; CAC/LTV proxy. — deps: A1, D3, D4 (reviews) — **accept:** analyze endpoint returns segments + a campaign recommendation; tests.
- **[A7] Customer agent depth** — seed `order_payments` (D4) so billing is real; add `review_sentiment` tool over `order_reviews` (rule-based + optional LLM); persist `conversations`/`conversation_messages`; token-streaming SSE that actually streams provider tokens (not re-run). — deps: A1, A3, D4 — **accept:** billing answer cites a real payment row; `/api/v1/agents/customer/stream` streams tokens as they arrive; conversation is retrievable by id.
- **[A8] Analytics (text-to-SQL) agent** — new `app/agents/analytics/{agent.py,graph.py,tools.py,schemas.py,prompts.py}` implementing the `sql-agent` node graph (reformulate → classify simple/complex/out-of-scope → CoT plan → generate w/ few-shot → execute w/ S8 guard → self-correct ≤3 → summarize + chart-type). Schema context: `scripts/build_schema.py` → `app/agents/analytics/schema.txt` (cached). `validator` tool from `langgraph-ecommerce`. Route `app/api/v1/agents/analytics.py` (`POST /query` → `{summary, sql, rows, chart_type, retries}`). — deps: S8, A1 — **accept:** answers the sample questions from `eval/benchmark.json` at ≥ the threshold; out-of-scope questions are refused; injection attempts blocked; every response includes the SQL it ran.
- **[A9] Agent run persistence** — every agent `run_analysis`/`query` writes an `agent_runs` row (agent, trigger, execution_id, started/finished, status, latency_ms, token_usage, cost_usd) and `agent_findings` rows; the existing `AgentPrediction` write stays or folds in. Emit an event on the bus. — deps: D2, R1 — **accept:** each run is queryable via `GET /api/v1/agents/{agent}/runs`; the command center (FE9) lists them live.

### M6 — Orchestrator & automation (P1)

- **[O1] Orchestrator** — port `backend/app/orchestrator/{coordinator.py,router.py,correlation.py,conflict_resolution.py,execution_context.py}` from `updated_CommerceOS`. Adapt to the 6 agents present here. `EventRouter` maps event types → agents; `coordinator.run(event|manual)` → invoke relevant agents → collect → `cross_domain_correlator` → `conflict_detector` → produce an `orchestration_decisions` row + notifications. — deps: A4–A6, A9 — **accept:** a simulated "cancellation spike" event triggers orders+customer+marketing, correlation produces one systemic finding, a decision row is persisted; conflicting recs (e.g. pricing wants markdown, inventory wants hold) are resolved deterministically with a recorded rationale.
- **[O2] Orchestrator API + schedule** — `app/api/v1/orchestrator.py` (`POST /run`, `GET /decisions`, `GET /decisions/{id}`), worker cron every N min. — deps: O1, R3 — **accept:** manual run + scheduled runs both persist decisions; endpoint RBAC = any authenticated read, `SUPER_ADMIN` to trigger.
- **[O3] Automation / approvals** — port `backend/app/automation/{actions.py,approval.py,executor.py,policies.py,rollback.py,verification.py}`. Policy: map `(agent, action_type, confidence)` → `AUTO` | `NEEDS_APPROVAL(role)` | `BLOCKED`. Auto actions execute + write `automation_actions` + verify + can rollback. Approval actions land in `approvals` for a human. — deps: O1, D2, S4 — **accept:** a high-confidence "flag backlog for review" auto-executes and is verified; a "issue markdown" action waits for a `PRICING_ADMIN` approval; rejecting rolls back nothing (never executed); approving executes + audits.
- **[O4] Approvals API + UI** — `app/api/v1/approvals.py` (`GET /`, `POST /{id}/approve`, `POST /{id}/reject`); FE8 HITL queue. — deps: O3 — **accept:** approve/reject flow end-to-end with audit; WS push to the queue.

### M7 — API consolidation (P1)

- **[P1] Versioned router tree** — `backend/app/api/v1/__init__.py` mounts: `auth`, `users`, `audit`, `health`, `notifications`, `agents/{orders,inventory,customer,logistics,pricing,marketing,analytics}`, `orchestrator`, `approvals`, `simulation`, `stream`, `metrics`, `dashboard`. `main.py` includes only `api_v1_router` (+ health at root). Keep current paths as thin 308-redirect shims for one release. — deps: all M3–M6 route tasks — **accept:** `openapi.json` shows a single coherent `/api/v1` tree; old paths redirect; frontend uses only v1.
- **[P2] Response envelope + pagination** — `backend/app/schemas/common.py` (`Envelope[T]`, `Page[T]`, `ErrorBody`). Apply to list endpoints (`agent_runs`, `notifications`, `audit_log`, `approvals`, `decisions`). — deps: P1 — **accept:** list endpoints paginate (`?limit&cursor`) with a consistent shape.
- **[P3] Dashboard aggregation endpoint** — `app/api/v1/dashboard.py`: one call returns the cross-agent KPI snapshot the home page needs (port/adapt from `updated_CommerceOS`), from the **read** DB. — deps: D3, A9 — **accept:** `GET /api/v1/dashboard/overview` < 300 ms, all numbers OBSERVED/CALCULATED.

### M8 — Frontend (P1)

- **[FE1] Tooling** — add `react-router-dom`, `@tanstack/react-query`, `zod`, `tailwindcss` (+ config) or commit to CSS-modules; `eslint` + `prettier` + `vitest` + `@testing-library/react` + `playwright`. `frontend/src/lib/api.ts` (typed client, base URL from env, attaches JWT, 401→logout, error normalization). — deps: none — **accept:** `npm run lint && npm run typecheck && npm run test && npm run build` all green.
- **[FE2] Auth** — `src/auth/AuthContext.tsx`, `src/pages/Login.tsx`, `src/components/ProtectedRoute.tsx`, token in memory + refresh cookie (or localStorage w/ documented tradeoff), logout, idle timeout. — deps: FE1, S2 — **accept:** unauthenticated visit → `/login`; after login → dashboard; refresh keeps session; logout clears.
- **[FE3] App shell + routing** — `src/App.tsx` becomes a router: `/`, `/agents/:agent`, `/orchestrator`, `/analytics`, `/approvals`, `/notifications`, `/admin/users`, `/admin/audit`, `/login`. Left nav, top bar (user menu, env badge, connection status). Move the shared ingestion bar into the shell. — deps: FE1, FE2 — **accept:** deep-linking works; nav reflects role (hide admin for non-super-admin).
- **[FE4] Data layer** — replace every `useEffect`+`fetch`+`setInterval` with `react-query` hooks in `src/api/*`; WS-driven invalidation (R4). Delete `refreshKey` prop plumbing. — deps: FE1, R4 — **accept:** no `setInterval` in `src/`; caching + background refetch visible in devtools.
- **[FE5] Shared components** — `ErrorBoundary`, `Toaster`, `Skeleton`, `DataTable` (sortable/paginated), `MetricCard`, `Finding`, `AgentTrace`, `ConfidenceBadge`, `ProvenanceBadge`, `EmptyState`. Refactor `OrdersAgentDashboard.tsx` (1587 LOC) into these + `orders/` sub-components. — deps: FE1 — **accept:** Orders dashboard renders from shared components; each has a Vitest test.
- **[FE6] Existing dashboards on v1** — point Orders/Inventory/Customer at `/api/v1/*`; keep feature parity. — deps: FE1, P1 — **accept:** all three work against the new API with auth.
- **[FE7] Logistics dashboard** — `src/pages/agents/Logistics.tsx` — lane SLA table, carrier late-rate chart, hub bottleneck list, ETA distribution, findings. — deps: FE5, A4.
- **[FE8] Pricing + Marketing dashboards** — `src/pages/agents/Pricing.tsx` (margin waterfall, discount-leakage, elasticity, recommendations) + `Marketing.tsx` (RFM grid, demand-trend, campaign recs). — deps: FE5, A5, A6.
- **[FE9] Analytics ("Ask the data")** — `src/pages/Analytics.tsx` — NL box → shows generated SQL (collapsible), result table + auto chart (recharts) by `chart_type`, self-correction attempts, export CSV. — deps: FE5, A8.
- **[FE10] Orchestrator command center** — `src/pages/Orchestrator.tsx` — live agent-run feed (WS), cross-domain decisions, conflict resolutions with rationale, "run now" (super-admin). — deps: FE5, O2, R2.
- **[FE11] Approvals + Notifications + Admin** — `Approvals.tsx` (HITL queue, approve/reject w/ reason), `Notifications.tsx` (center, mark read/ack/resolve), `admin/Users.tsx`, `admin/Audit.tsx`. — deps: FE5, O4, S3.
- **[FE12] a11y + polish + design pass** — landmarks, aria, keyboard nav, contrast AA, reduced-motion, responsive; consistent theme tokens; fix the sticky-header tall-page quirk. — deps: FE3–FE11 — **accept:** `axe` CI check passes; Lighthouse a11y ≥ 95.

### M9 — Observability (P1)

- **[OB1] Metrics** — `prometheus-fastapi-instrumentator` → `/metrics`; custom collectors: `agent_run_total{agent,status}`, `agent_run_latency_seconds{agent}`, `llm_tokens_total{provider,agent,kind}`, `llm_cost_usd_total{provider,agent}`, `replay_lag_events`, `ws_connections`. — deps: F3 — **accept:** `/metrics` scrapeable; counters move during a run.
- **[OB2] Tracing** — OpenTelemetry SDK + auto-instrument FastAPI + SQLAlchemy + `httpx`; manual spans around each graph node + LLM call; OTLP exporter → ADOT sidecar → X-Ray (or Langfuse if chosen). — deps: F3 — **accept:** one agent run = one trace with nested node + SQL + LLM spans.
- **[OB3] Healthchecks** — `app/api/v1/health.py`: `/health` (process), `/health/ready` (DB write, DB read, Redis, LLM provider ping with cache). Used by compose, ALB, ECS. — deps: D3, R1, A1 — **accept:** readiness flips to 503 when Redis is down.
- **[OB4] CloudWatch dashboards + alarms** — Terraform `infra/aws/modules/observability`: log groups, metric filters, dashboard (RPS, p95, 5xx%, agent-failure rate, replay lag, RDS CPU/conns, Redis mem/evictions, LLM $/day), alarms → SNS → email/Slack. — deps: I-series, OB1 — **accept:** killing a task fires the "unhealthy host" alarm; a synthetic 5xx burst fires the error-rate alarm.
- **[OB5] Cost telemetry** — daily rollup job (worker) → `agent_runs` cost sums → a `llm_cost_daily` metric + a budget alarm at `settings.LLM_MONTHLY_BUDGET_USD`. — deps: OB1, R3 — **accept:** dashboard shows $/agent/day; exceeding the daily prorated budget alarms.

### M10 — Testing & evals (P0 for the gate, P1 to build)

- **[T1] Test infra** — `backend/tests/conftest.py`: ephemeral Postgres via `testcontainers` (or a dedicated compose DB) + `alembic upgrade head` per session; transactional rollback per test; `factory_boy` factories for `User`, `Order`, `Product`, etc.; `auth_client` fixture (logged-in `TestClient` per role); `pytest-cov` with `--cov-fail-under=80` for the target packages. Migrate the 3 existing test files off the committed `orders.db`. — deps: D1 — **accept:** `make test-cov` green; no test touches a real file DB.
- **[T2] Agent unit + graph tests** — per agent: empty-DB ⇒ NOT_ESTIMABLE; happy path; each graph branch; each tool; LLM mocked (deterministic + a fake provider that returns canned JSON + one that errors → assert typed exception). — deps: T1, A-series — **accept:** ≥ 80% on `app/agents/*`.
- **[T3] Error-path tests** — adapt `langgraph-ecommerce/tests/test_errors.py`: provider timeout, malformed LLM JSON, DB down mid-run, Redis down, SQL guard rejections, oversized inputs, expired token, wrong role. — deps: T1, S-series — **accept:** every failure yields the right status + a log line, never a bare 500 with a stack trace in prod mode.
- **[T4] API contract tests** — Schemathesis against `openapi.json` in CI; plus explicit "401 without token" sweep over every route. — deps: P1 — **accept:** Schemathesis passes; the auth sweep passes (allow-list the 3 public routes).
- **[T5] Agent eval harness** — `eval/`: `benchmark/{analytics.json (seed from sql-agent's 60), orders.json, inventory.json, customer.json, logistics.json, pricing.json, marketing.json}`; `eval/run.py` (per-agent scorers: exact/numeric-tolerance/LLM-judge; latency + token budget; pass threshold per agent); `eval/report.py` → `eval/reports/<ts>.html`. Wire `make eval`; CI runs it nightly + on `agents/**` changes and fails under threshold. — deps: A-series — **accept:** `make eval` produces a report; a deliberately broken prompt drops the score below threshold and fails CI.
- **[T6] Frontend tests** — Vitest component tests for FE5 components + each page's happy path (MSW-mocked API); Playwright e2e: login → dashboard → open each agent → run analyze → see findings → open Analytics → ask a question → see SQL+chart → logout. — deps: FE-series — **accept:** `npm run test` + `npx playwright test` green in CI (headless).
- **[T7] Load test** — `loadtest/k6/{dashboard.js,agent_analyze.js,analytics_query.js}`; SLO: p95 `/dashboard/overview` < 300 ms @ 50 rps; `/agents/*/analyze` < 5 s @ 5 rps; error rate < 1%. Run against `dev`. — deps: I-series — **accept:** SLOs met; autoscaling adds a task under load and removes it after.

### M11 — CI/CD (P0)

- **[C1] CI workflow** — `.github/workflows/ci.yml` (PR + push): backend `ruff`+`black --check`+`isort --check`+`mypy`+`pytest --cov` (Postgres service container); frontend `eslint`+`tsc --noEmit`+`vitest`+`build`; `pip-audit` + `npm audit --production` + `detect-secrets`; Trivy FS scan; build both images (no push on PR); upload coverage + eval report artifacts. — deps: T1, F9 — **accept:** green on a clean PR; red when a test/lint/secret fails.
- **[C2] CD workflow** — `.github/workflows/deploy.yml` (push to `main` → `dev`, tag `v*` → `prod`, both with manual approval for prod): configure AWS OIDC role, build+push images to ECR, run `alembic upgrade head` as a one-shot ECS task, `aws ecs update-service --force-new-deployment`, wait for stable, run `loadtest/k6/smoke.js` + Playwright smoke against the deployed URL, auto-rollback on failure (deploy previous task def). — deps: I-series, C1 — **accept:** merging to `main` deploys to `dev` and the smoke test passes; a failing smoke rolls back.
- **[C3] Release hygiene** — `CHANGELOG.md`, `release-please` or manual tags, image tags = git SHA + semver, `docs/api.md` regenerated in CD and committed/published. — deps: C2 — **accept:** every prod deploy is traceable to a SHA and a changelog entry.

### M12 — Infrastructure (AWS) (P0)

- **[I1] Terraform skeleton** — `infra/aws/`: `modules/{network,ecr,rds,redis,ecs,alb,frontend_cdn,secrets,observability,waf,iam}` + `envs/{dev,prod}/{main.tf,variables.tf,outputs.tf,backend.tf,terraform.tfvars.example}`. Remote state in S3 + DynamoDB lock. Adapt structure from `agentic-ecommerce/terraform-gcp` (GCP→AWS). — deps: none — **accept:** `terraform validate` + `tflint` pass for both envs.
- **[I2] Network** — VPC, 2 AZ, public + private subnets, IGW, 1 NAT (dev) / 2 NAT (prod), route tables, VPC endpoints for ECR/S3/Secrets/CloudWatch/Bedrock, flow logs. — deps: I1 — **accept:** `apply` in `dev`; RDS/Redis subnets have no route to IGW.
- **[I3] Data stores** — RDS PostgreSQL 15 (Multi-AZ in prod, encrypted, automated backups + PITR, param group with `statement_timeout` defaults, `commerceos_ro` role via a null_resource/psql provisioner or a bootstrap Lambda) + read replica (prod); ElastiCache Redis (encryption in transit + at rest, auth token). Creds → Secrets Manager. — deps: I2 — **accept:** backend task connects; failover test (prod) < 60 s; read replica reachable with the RO role.
- **[I4] Compute** — ECR repos; ECS cluster; `api` service (Fargate, 2+ tasks, target-tracking autoscale CPU+ALBRequestCount, rolling deploy, circuit breaker), `worker` service (1 task, no LB), `migrate` one-shot task def; ADOT sidecar for OTEL. Task roles least-privilege (Bedrock invoke, Secrets get, S3 dataset read, CloudWatch put). — deps: I3, F6 — **accept:** services reach steady state; scaling policy visible; task role denies everything not needed.
- **[I5] Edge** — ALB (HTTPS via ACM, HTTP→HTTPS redirect, `/api/*` + `/ws` → api target group, health check `/health`), WAF (managed rule sets + rate rule) on ALB; S3 (private) + CloudFront (OAC, SPA error mapping to `/index.html`, ACM in us-east-1) + WAF on CloudFront; Route53 records. — deps: I4 — **accept:** `https://<domain>` serves the SPA; `https://<domain>/api/v1/health` 200; WAF blocks a canned SQLi probe.
- **[I6] Secrets & config** — Secrets Manager: `db-credentials`, `db-ro-credentials`, `redis-auth`, `jwt-secret`, `llm-keys`; SSM params for non-secret config; ECS task defs wire them. — deps: I1 — **accept:** no plaintext secret in task def env; app reads them at boot.
- **[I7] Observability infra** — OB4 module; ADOT collector config; log retention (30 d dev / 400 d prod); X-Ray sampling. — deps: I4 — **accept:** logs + traces + metrics visible in CloudWatch/X-Ray for a live request.
- **[I8] Dataset bootstrap** — `infra` S3 bucket `commerceos-datasets`; a `bootstrap` job (CodeBuild or a `make` step) uploads the Olist + DataCo CSVs (documented manual download, checksummed) so `build_warehouse.py` can run in-cloud from S3. — deps: I1, D4 — **accept:** the `migrate`/seed task pulls CSVs from S3 and populates RDS.
- **[I9] Cost guardrails** — AWS Budgets (monthly + anomaly), tag policy (`Project=CommerceOS`, `Env`), `infracost` in CI on `infra/**` PRs. — deps: I1 — **accept:** `infracost diff` comment on infra PRs; a budget alarm exists.

### M13 — Documentation & deliverables (P0)

- **[DOC1] README rewrite** — `README.md`: what/why, architecture diagram, the 8 agents, local quickstart (`make setup && make dev`), env vars table, deploy pointer, test/eval, troubleshooting, license. — deps: most of the build — **accept:** a new dev goes from clone → running locally using only the README.
- **[DOC2] Architecture** — `docs/architecture.md` + `docs/img/*`: context + container + component diagrams (mermaid/`structurizr`), the agent-run sequence, the orchestrator sequence, data flow, deployment topology, key decisions link to ADRs. — deps: build — **accept:** diagrams render on GitHub; every box maps to a real module/resource.
- **[DOC3] DB schema** — `docs/db-schema.md` + `docs/img/erd.svg` (D8): every table, column, type, FK, index, and why it exists; the warehouse vs operational split. — deps: D8 — **accept:** matches `alembic upgrade head` output exactly.
- **[DOC4] Cost estimate** — `docs/cost-estimate.md` + `docs/cost-model.xlsx` (or a `.csv` + script): AWS monthly for `dev` (~always-off), `prod-low`, `prod-target` — line items (Fargate vCPU/GB-hr, RDS, replica, ElastiCache, ALB, NAT, CloudFront, S3, data transfer, CloudWatch, Secrets, WAF) + **LLM token cost model** (tokens/agent-run × runs/day × $/1k by provider) + Bedrock vs OpenAI comparison + a 20% buffer. — deps: I-series, OB5 — **accept:** numbers tie to `infracost` output ± 15%; a reviewer can change "runs/day" and see the total move.
- **[DOC5] API doc** — `docs/api.md` via `redoc-cli` from `openapi.json` + an auth/RBAC section + example flows. Regenerated in CD. — deps: P1 — **accept:** matches the deployed `/openapi.json`.
- **[DOC6] Runbook** — `docs/runbook.md`: deploy, rollback, DB migrate/restore (PITR), rotate secrets, scale up/down, replay engine ops, incident triage (dashboards → likely cause), on-call, cost spike response, `git filter-repo` note for the leaked `.env`. — deps: C2, I-series — **accept:** a dry-run rollback following the doc succeeds.
- **[DOC7] Security doc** — `docs/security.md`: threat model (STRIDE-lite), RBAC matrix (role × route), PII inventory + handling + retention, secret management + rotation, network isolation, dependency/image scanning, incident response, responsible-AI notes (prompt-injection, output review, no autonomous financial actions without approval). — deps: S-series, I-series — **accept:** RBAC matrix matches the code; every PII field is listed with a retention rule.
- **[DOC8] ADRs** — `docs/adr/0001-aws.md`, `0002-postgres.md`, `0003-llm-provider-abstraction.md`, `0004-monorepo-canonical.md`, `0005-langgraph-orchestrator.md`, `0006-hitl-automation.md`. — deps: none — **accept:** each ADR has context/decision/consequences.
- **[DOC9] Presentation** — `docs/presentation/` reveal.js deck (`index.html` + slides) **and** a generated `CommerceOS.pptx` (`scripts/build_deck.py` with `python-pptx`): problem → solution → architecture → the 8 agents (with a live-demo screenshot each) → orchestrator + HITL → results/evals → AWS deployment + cost → security → roadmap. — deps: build, DOC2, DOC4 — **accept:** deck builds in CI; opens in PowerPoint; ≤ 20 slides; every claim traces to a doc.
- **[DOC10] Demo assets** — `scripts/demo_seed.py` (a deterministic demo scenario: seed a backlog spike + a margin dip + a late-carrier so every agent has something to say), a short screencast checklist, and a Postman/Bruno collection `docs/CommerceOS.bruno`. — deps: A-series, O1 — **accept:** running the demo seed then opening the app shows non-trivial findings on every dashboard.

---

## 4. Implementation order (milestone dependency graph)

```
M1 Foundation ─┬─> M2 DB/data ─┬─> M3 Security ─┬─> M4 Realtime ──> M5 Agents ──> M6 Orchestrator ─┐
               │               │                │                                                  │
               │               └─> M10 Testing (T1 first, then grows with each milestone)          │
               │                                                                                    ▼
               └─> M12 Infra (I1-I3 early, in parallel) ───────────────> M7 API ──> M8 Frontend ──> M9 Observability
                                                                                                    │
                                                              M11 CI/CD (C1 after T1; C2 after M12) │
                                                                                                    ▼
                                                                                        M13 Docs & deliverables
```

**Sequenced task list (do in this order):**

1. **M1** F1→F2→F3→F4→F5→F6→F7→F8→F9
2. **M2** D1→D3→D4→D2→D5→D7→D6→D8
3. **M10** T1 (test infra now; keep adding tests alongside every later task)
4. **M3** S1→S2→S3→S4→S5→S6→S7→S10  (S8 with A8, S9 with M12)
5. **M4** R1→R2→R3→R4
6. **M5** A1→A2→A3→A9→A4→A5→A6→A7→A8 (+S8)
7. **M6** O1→O2→O3→O4
8. **M7** P1→P2→P3
9. **M8** FE1→FE2→FE3→FE4→FE5→FE6→FE7→FE8→FE9→FE10→FE11→FE12
10. **M9** OB1→OB2→OB3 (OB4/OB5 need infra)
11. **M12** I1→I2→I3→I8→I4→I6→I5→I7→I9  (start I1–I3 in parallel with M3)
12. **M11** C1 (after T1) → C2 (after M12) → C3
13. **M9** OB4→OB5
14. **M10** T2→T3→T4→T5→T6→T7
15. **M13** DOC8 (anytime) → DOC1→DOC2→DOC3→DOC5→DOC4→DOC6→DOC7→DOC9→DOC10

**Priority buckets for a time-boxed cut:**
- **Must (P0) for "deployed + credible":** M1, M2, M3 (S1–S5), M5 (A1, A2, A9 + the 4 missing agents A4–A6, A8), M7 (P1), M8 (FE1–FE9), M11 (C1–C2), M12 (all), M13 (DOC1–DOC5, DOC9).
- **Should (P1):** M4, M5 (A3, A7), M6, M9, remaining M8, M10 (T2–T7), M13 rest.
- **Could (P2/P3):** advanced forecasting (Holt-Winters/STL), price elasticity depth, RFM refinements, Langfuse, blue/green, canary, multi-region.

---

## 5. Cross-cutting acceptance checklist (run before calling it done)

- [ ] `grep -rnE "hashlib|md5\(|= 11115|142\.80|118\.5|TODO|FIXME|NotImplementedError|mock|dummy" backend/app` → only in tests/eval fixtures.
- [ ] `grep -rn "os.environ" backend/app | grep -v core/settings` → empty.
- [ ] Every route in `openapi.json` except the 3 public ones returns 401 without a token (automated in T4).
- [ ] `docker compose up` → 5 healthy containers; login; each dashboard shows real data; run each agent; trigger the orchestrator; approve an action.
- [ ] `cd infra/aws/envs/prod && terraform apply` → `https://<domain>` live; `/api/v1/health/ready` 200; WAF blocks an SQLi probe; a killed task self-heals.
- [ ] CI green on a PR; merging to `main` deploys `dev` and smoke passes; a forced smoke failure rolls back.
- [ ] `make eval` ≥ threshold for every agent; `make test-cov` ≥ 80% on target packages; Playwright e2e green.
- [ ] CloudWatch dashboard populated; an induced 5xx burst + an induced unhealthy task both alarm to SNS.
- [ ] `docs/` complete; ERD matches migrations; cost model ties to `infracost` ± 15%; deck builds.
- [ ] No secret in the repo or git history; `detect-secrets` + `gitleaks` clean.

---

## 6. Notes on porting from `updated_CommerceOS`

Port (adapt imports to `app.core.settings`, this repo's `models`, and the `llm` provider):
`services/auth_service.py`, `api/v1/{auth,deps,audit,users,notifications,health,dashboard}.py`,
`orchestrator/*`, `automation/*`, `agents/{logistics,pricing,marketing}/*`,
`events/*`, `observability/*` (if present), `resilience/idempotency.py`, `core/logging.py`,
`exceptions/*`, `core/settings/*`, `docker-compose.yml`, `Makefile`, `tests/backend/test_auth_rbac.py`.

Do **not** port blindly: the `get_current_user` fallback that fabricates a user for an unknown
token subject (security hole — reject instead); any `allow_origins=["*"]`; any in-request
background task; SQLite-specific code.

Verify every ported module against the acceptance criteria above — the source is more complete
than this repo but is **not** assumed correct or production-ready.
