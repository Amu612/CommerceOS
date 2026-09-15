<div align="center">

# ⚡ CommerceOS
### Autonomous Multi-Agent Operating System for Intelligent E-Commerce Operations

[![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6.svg?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-ReAct%20Agents-FF4B4B.svg?style=for-the-badge)](https://langchain-ai.github.io/langgraph/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

<p align="center">
  <a href="http://13.233.163.110/"><b>🔴 Live Demo — http://13.233.163.110/</b></a>
</p>

<p align="center">
  <b>CommerceOS</b> replaces fragmented, manual e-commerce administration with six autonomous, specialized AI agents — Orders, Inventory, Customer Support, Pricing, Marketing, and Logistics. Operating over real transaction and supply-chain data, they continuously audit pipelines, detect anomalies, forecast demand, and propose (or, with human approval, execute) corrective actions.
</p>

[Key Features](#-key-features) • [Architecture](#-system-architecture) • [Getting Started](#-getting-started) • [Default Credentials](#-default-credentials) • [API Reference](#-api-reference) • [Tech Stack](#-tech-stack) • [Testing](#-testing--quality) • [Deploying to AWS](#-deploying-to-aws) • [Repository Structure](#-repository-structure)

---

</div>

## 🌟 Key Features

### 🧠 Six Domain Agents, One Shared Framework
Every agent is built on a common LangGraph `DomainAgent` framework (`backend/app/agents/framework.py`): LangChain `@tool` functions do the real, data-driven computation (SQL aggregates + statistical fences — the LLM never does the math), and a `create_react_agent` ReAct loop (or a deterministic tool-router when no LLM is configured) drives conversational queries over that same tool surface.

| Agent | Focus |
| :--- | :--- |
| 📦 **Orders** | Empirical pipeline auditing (delay rates, fulfillment ratios, backlog aging) across 13,000+ orders; order/SKU/customer entity resolution; milestone shipment timelines; 30-day RMA/return-policy enforcement. |
| 🛡️ **Inventory** | Continuous stockout-risk monitoring across 11,000+ products; Reorder Point (ROP = lead-time demand + safety stock) and Economic Order Quantity (EOQ) estimation; 30-day sales-velocity trend classification. |
| 💬 **Customer Support** | Conversational order/RMA/shipment lookups with entity verification, grounded in the same live database the other agents read. |
| 💲 **Pricing** | Catalog pricing benchmarks and margin analysis, optionally enriched with a live competitor price feed via **Apify**. |
| 📣 **Marketing** | Demand forecasting and promotion-velocity analysis on top of the `app.intelligence.forecasting` engine. |
| 🚚 **Logistics** | Carrier dispatch tracking, milestone management, and route intelligence (TomTom routing API with a keyless OSRM fallback), visualized in 3D via Cesium. |

### 🧭 Orchestrator & Human-in-the-Loop Automation
- **Cross-Domain Sweep**: A `SUPER_ADMIN`-only orchestrator (`app/orchestrator/coordinator.py`) runs every agent in one pass and correlates findings across domains.
- **Automation Engine**: Policy-driven action proposals (`app/automation/`) that route through an **Approvals** queue — a domain admin reviews and approves/rejects actions scoped to their own agent before anything executes.
- **Full Audit Trail**: An append-only `AuditLog` records every authenticated mutating request plus explicit security events, exposed via `/api/v1/audit`.

### 🔁 Live Data Replay & Streaming
- **Replay Engine**: Indexes the bundled Olist + DataCo datasets and streams them into the database at a controllable speed (start/pause/resume/stop/step/reset) so the whole platform can demo against a live-feeling order stream instead of a static snapshot.
- **WebSocket Stream**: Real-time push of agent findings, notifications, and ingestion progress to the frontend (`app/api/v1/stream.py`).

### 🔐 Role-Based Access Control
- **7 roles**, one `SUPER_ADMIN` plus one domain-admin role per agent (`ORDERS_ADMIN`, `INVENTORY_ADMIN`, `CUSTOMER_SUPPORT_ADMIN`, `PRICING_ADMIN`, `MARKETING_ADMIN`, `LOGISTICS_ADMIN`) — centralized in `app/core/rbac.py` as the single source of truth read by every route dependency, per-record check, and the frontend's own nav rendering.
- Domain admins get `200` on their own agent's routes and `403` everywhere else; only `SUPER_ADMIN` can reach the Orchestrator, user management, and the audit log.

### 🤖 Multi-Provider LLM Resilience
- **`LLM_PROVIDER=auto`** picks the first provider with credentials, in order: **Groq** (`openai/gpt-oss-120b`) → **OpenAI** (`gpt-4o-mini`) → **Anthropic** (`claude-3-5-sonnet`) → **AWS Bedrock** → otherwise falls back to a **deterministic**, dependency-free synthesizer that still formats real tool output into structured, actionable answers — the platform never goes offline just because an external LLM API is unreachable.
- Token-budget guardrails and a monthly spend cap (`LLM_REQUEST_TOKEN_BUDGET`, `LLM_MONTHLY_BUDGET_USD`) live in `app/services/llm/guardrails.py`.

### 📊 Statistical Intelligence (no black-box thresholds)
- Custom **Modified Z-Score**, **IQR/Tukey fences**, **MAD**, and **binomial standard error** profilers (`app/intelligence/`) drive every anomaly finding and confidence score — nothing is hardcoded; every threshold comes from the observed data distribution.

---

## 🏗️ System Architecture

```text
┌─────────────┐      HTTPS / WebSocket      ┌───────────────────┐
│   React +   │ ───────────────────────────▶│   FastAPI Backend │
│  TypeScript │ ◀─────────────────────────── │   (Uvicorn/       │
│  Dashboard  │                              │    Gunicorn)      │
└─────────────┘                              └─────────┬─────────┘
                                                         │
                     ┌───────────────────────────────────┼───────────────────────────────────┐
                     ▼                                   ▼                                   ▼
           ┌─────────────────┐                 ┌──────────────────┐                ┌──────────────────┐
           │  6 Domain Agents │                 │   Orchestrator   │                │  Automation +     │
           │ (LangGraph ReAct│                 │  (cross-domain    │                │  Approvals engine │
           │  + tools + stats)│                 │   sweep)          │                │  (human-in-loop)  │
           └────────┬────────┘                 └──────────────────┘                └──────────────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
 ┌────────────┐ ┌───────────┐ ┌──────────────┐
 │ PostgreSQL │ │   Redis   │ │ Multi-provider│
 │ / SQLite   │ │ (cache /  │ │ LLM factory   │
 │ (Olist +   │ │  events)  │ │ (Groq/OpenAI/ │
 │  DataCo)   │ │           │ │ Anthropic/... │
 └────────────┘ └───────────┘ └──────────────┘
```

---

## 🚀 Getting Started

### Prerequisites
- **Python 3.11+** (< 3.13)
- **Node.js 18+** and **npm**
- *(Optional)* **Docker & Docker Compose** for the full production-like stack
- *(Optional)* PostgreSQL — a bundled **SQLite** database (`orders.db`) is used automatically when `DATABASE_URL` is left blank, so Postgres isn't required for local dev

---

### Option 1: Quick Start on Windows (Recommended)

1. **Configure environment** (optional — sensible defaults ship without it):
   ```powershell
   copy backend\.env.example backend\.env
   copy frontend\.env.example frontend\.env
   ```

2. **Start the Backend**:
   Double-click `start_backend.bat` (or run in a terminal):
   ```powershell
   python run_backend.py
   ```
   - Server running at: **`http://127.0.0.1:8000`**
   - Interactive Swagger API docs: **`http://127.0.0.1:8000/docs`**
   - On first boot it auto-creates tables, indexes the replay dataset, and seeds the database from the bundled Olist + DataCo CSVs if empty.

3. **Start the Frontend**:
   Double-click `start_frontend.bat` (or run in a terminal):
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```
   - Dashboard running at: **`http://localhost:3000`** (Vite dev server)

---

### Option 2: Manual setup (macOS/Linux/Windows, any shell)

```bash
# Backend
cd backend
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Or via the repo `Makefile` (from the repo root):
```bash
make setup   # install backend + frontend deps
make dev     # run the backend with reload
make seed    # build the warehouse + seed the 7 role accounts
```

---

### Option 3: Docker Compose (Full Stack)

Runs the full production-shaped stack: PostgreSQL, Redis, backend API, background worker, a one-shot `migrate` job (Alembic + warehouse build + user seed), and the frontend behind Nginx.

```bash
docker compose up --build
```

- Frontend: **`http://localhost:3000`**
- Backend API: **`http://localhost:8000`**

---

## 🔐 Default Credentials

The platform is pre-seeded (via `make seed` / `python -m scripts.seed_users`, or automatically in the `migrate` Compose job) with one dedicated role-based access control (RBAC) account per domain:

| Username | Role | Scope | Default Password |
| :--- | :--- | :--- | :--- |
| **`admin`** | `SUPER_ADMIN` | Full platform authority: all six agents, the Orchestrator, user management, audit log | `CommerceOS2026!` |
| **`orders_admin`** | `ORDERS_ADMIN` | Orders intelligence, backlog oversight, SLA metrics, returns | `CommerceOS2026!` |
| **`inventory_admin`** | `INVENTORY_ADMIN` | Stock watchdog, catalog valuation, ROP/EOQ, purchase orders | `CommerceOS2026!` |
| **`support_admin`** | `CUSTOMER_SUPPORT_ADMIN` | Customer inquiries, order dispute resolutions, RMAs | `CommerceOS2026!` |
| **`pricing_admin`** | `PRICING_ADMIN` | Catalog pricing benchmarks, margin optimization, competitor feed | `CommerceOS2026!` |
| **`marketing_admin`** | `MARKETING_ADMIN` | Demand forecasting and promotion velocity analysis | `CommerceOS2026!` |
| **`logistics_admin`** | `LOGISTICS_ADMIN` | Dispatch carrier tracking, milestone management, route intelligence | `CommerceOS2026!` |

Password is controlled by `SEED_ADMIN_PASSWORD` in `backend/.env` — **change it before any non-local deployment.** In production, `AUTH_ENFORCED` is always on; in development it's soft-enforced so the demo dashboard works without a login wall.

---

## 📡 API Reference

Interactive OpenAPI documentation is generated at **`/docs`** and **`/redoc`** (disabled automatically in production via `EXPOSE_DOCS`). Key endpoint groups:

### Orders Agent (`/api/orders`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/orders/analyze` | Full empirical pipeline analysis cycle |
| `POST` | `/api/orders/query` | Interactive natural-language / ReAct tool query |
| `GET` | `/api/orders/latest` | Cached analysis snapshot |
| `GET` | `/api/orders/health` | Pipeline health scorecard and active issue count |
| `POST` | `/api/orders/reset` | Resets agent cache and active notifications |
| `GET` | `/api/orders/ingestion/status` | Replay/ingestion status |
| `POST` | `/api/orders/ingestion/control` | Start/pause/resume/stop/step/reset the data replay |

### Inventory Agent (`/api/v1/agents/inventory`, alias `/api/inventory`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET`/`POST` | `/monitor` | Runs the watchdog cycle (stock, alerts, ROP, valuation) |
| `POST` | `/query` | Conversational query for inventory status and trends |
| `POST` | `/reorder` | Triggers a restocking purchase order |

### Customer Support Agent (`/api/customer`, alias `/api/v1/agents/customer`)
### Logistics, Pricing, Marketing Agents (`/api/v1/agents/{logistics,pricing,marketing}`)
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET`/`POST` | `/analyze` | Runs that agent's analysis cycle |
| `POST` | `/query` | Conversational query against that agent's tools |
| `GET` | `/runs` | Recent analysis run history |
| `GET`/`POST` | `/logistics/route` | Route lookup for an order (TomTom, OSRM fallback) |
| `GET` | `/pricing/competitor-feed` | Apify competitor price feed status |
| `POST` | `/pricing/competitor-feed/sync` | Triggers a competitor price sync |

### Orchestrator, Automation & Audit
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST`/`GET` | `/api/v1/orchestrator/*` | Cross-domain sweep across all six agents (`SUPER_ADMIN` only) |
| `*` | `/api/v1/automation/*` | Automation policies and the human-in-the-loop approvals queue |
| `GET` | `/api/v1/runs` | Agent run history |
| `GET` | `/api/v1/audit` | Append-only audit log (`SUPER_ADMIN` only) |

### Authentication & System
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/auth/login` | Issues a JWT bearer token |
| `GET` | `/api/v1/auth/me` | Current user + permitted agents |
| `GET` | `/api/v1/users` | User management (`SUPER_ADMIN` only) |
| `GET` | `/api/v1/system/health` | LLM provider health / infra status (public) |
| `GET` | `/health`, `/health/ready` | Liveness / readiness (database check) |
| `WS` | `/ws/*` | Real-time stream of findings, notifications, and ingestion progress |

---

## 🛠️ Tech Stack

**Backend**
- [FastAPI](https://fastapi.tiangolo.com/) 0.141 + [Uvicorn](https://www.uvicorn.org/)/[Gunicorn](https://gunicorn.org/), [Pydantic v2](https://docs.pydantic.dev/) & pydantic-settings
- [LangChain](https://python.langchain.com/) 1.4 + [LangGraph](https://langchain-ai.github.io/langgraph/) 1.2 (`create_react_agent`, Postgres checkpointing) for agent orchestration
- [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) — PostgreSQL (prod/compose) or SQLite (local fallback)
- Redis (cache / event bus), `websockets` for real-time streaming
- pandas / numpy — statistical intelligence (Modified Z-Score, IQR/Tukey, MAD, binomial SE)
- `python-jose` + `passlib[bcrypt]` — JWT auth and password hashing
- `structlog`, `prometheus-client` + `prometheus-fastapi-instrumentator`, OpenTelemetry — structured logging & observability
- `boto3` — AWS integration; `httpx` — outbound HTTP (Apify, TomTom, OSRM)

**Frontend**
- [React 18](https://react.dev/) + [TypeScript 5.6](https://www.typescriptlang.org/) + [Vite 6](https://vitejs.dev/)
- [CesiumJS](https://cesium.com/platform/cesiumjs/) (`vite-plugin-cesium`) for 3D logistics route visualization
- ESLint + Prettier + Vitest for linting, formatting, and unit tests

**Data & DevOps**
- Bundled **Olist** and **DataCo** e-commerce datasets, streamed via a custom replay engine
- Docker + Docker Compose (backend, worker, Postgres, Redis, Nginx-served frontend)
- Terraform under `infra/aws/` — VPC, ECS Fargate, RDS, ElastiCache, ALB, ECR, Secrets Manager, CloudWatch (see [Deploying to AWS](#-deploying-to-aws) and `docs/adr/`)
- GitHub Actions: `ci.yml` (lint/test/build/scan), `deploy.yml` (ship to ECS), `infra.yml` (Terraform plan/apply)
- `pre-commit` + `detect-secrets` baseline for secret scanning

---

## ✅ Testing & Quality

```bash
make test        # backend pytest + frontend vitest
make test-cov     # backend tests with coverage report
make lint         # ruff + black + isort (backend), eslint (frontend)
make typecheck    # mypy (backend), tsc --noEmit (frontend)
make security     # pip-audit, npm audit, detect-secrets scan
```

Backend test suite (`backend/tests/`) covers auth, RBAC boundaries, automation/approvals, and each domain agent (`test_orders_agent.py`, `test_inventory_agent.py`, `test_customer_agent.py`, `test_domain_agents.py`, `test_llm.py`). CI runs these against a real PostgreSQL service container on every push/PR to `main`.

---

## ☁️ Deploying to AWS

Two deployment paths, pick based on your account:

- **On an AWS Free Tier account?** Use [docs/deploy-free-tier.md](docs/deploy-free-tier.md) — one free-tier EC2 instance + free-tier RDS (+ optional ElastiCache), `docker-compose.free-tier.yml`, and [`deploy-free-tier.yml`](.github/workflows/deploy-free-tier.yml) (SSH-based). No ECS/Fargate/NAT/ALB/Secrets Manager — those aren't free-tier eligible. The only unavoidable cost is ~$3.60/mo for the instance's public IPv4 address (AWS-wide policy since Feb 2024).
- **Production / past free tier?** The rest of this section — full stack on ECS Fargate.

Full stack — VPC, ECS Fargate (api + worker + frontend), RDS PostgreSQL, ElastiCache Redis, ALB, ECR, Secrets Manager, CloudWatch — provisioned by Terraform (`infra/aws/`) and shipped by two GitHub Actions pipelines. No manual `docker push` or `kubectl apply` once it's wired up. Runs roughly $140+/mo (see [docs/cost-estimate.md](docs/cost-estimate.md)) — none of these services are free-tier eligible.

| Pipeline | Trigger | Does |
| :--- | :--- | :--- |
| [`ci.yml`](.github/workflows/ci.yml) | every push/PR | Lint, type-check, backend tests (live Postgres), frontend build, Docker image build + Trivy scan |
| [`deploy.yml`](.github/workflows/deploy.yml) | push to `main` (→ dev), tag `v*` (→ prod, approval-gated) | Build+push images to ECR → migrate DB → roll `api`/`worker`/`frontend` forward on ECS → smoke test → auto-rollback on failure |
| [`infra.yml`](.github/workflows/infra.yml) | PR touching `infra/aws/**` (plan), manual dispatch (plan/apply) | Terraform plan/apply for `dev`/`prod`, prod gated by a required reviewer |

**One-time setup** (per AWS account) — full walkthrough in [infra/aws/README.md](infra/aws/README.md):
1. `infra/aws/bootstrap` → Terraform remote state (S3 + DynamoDB) + a GitHub OIDC role (no static AWS keys in CI).
2. Push one bootstrap image to ECR so the first `terraform apply` has something to point the ECS task definitions at.
3. Upload the Olist/DataCo CSVs to the `datasets` S3 bucket Terraform creates (they're gitignored — never in the repo or the image).
4. `terraform apply` in `infra/aws/envs/dev` (and, when ready, `envs/prod`).
5. Add the resulting role ARNs as GitHub secrets — after that, every push deploys itself.

Terraform owns each ECS service's *shape* (CPU/memory, env vars, secrets, IAM); `deploy.yml` owns *which image tag is live* — they're wired so neither one fights the other (see "Day-to-day: which pipeline does what" in [infra/aws/README.md](infra/aws/README.md)). Operational runbook — deploy/rollback/incident triage — lives in [docs/runbook.md](docs/runbook.md); cost planning in [docs/cost-estimate.md](docs/cost-estimate.md); the architecture decisions behind all of this in [docs/adr/](docs/adr/) (0001 AWS, 0002 Postgres, 0007 frontend-on-ECS).

---

## 📁 Repository Structure

```text
├── backend/
│   ├── app/
│   │   ├── agents/            # 6 domain agents (orders, inventory, customer, pricing, marketing, logistics)
│   │   │                      #   + framework.py (shared LangGraph ReAct/tool runner)
│   │   ├── api/                # FastAPI routers (per-agent + v1), middleware (audit, security headers, request context)
│   │   ├── automation/         # Policy-driven action proposals + human-in-the-loop executor
│   │   ├── core/                # Settings, structured logging, security, centralized RBAC
│   │   ├── database/            # SQLAlchemy session factory + init
│   │   ├── intelligence/        # Anomaly detection, statistics, forecasting, confidence scoring
│   │   ├── models/               # SQLAlchemy models (Olist, DataCo, competitor, operations, security)
│   │   ├── orchestrator/         # Cross-domain sweep coordinator
│   │   ├── services/              # Multi-provider LLM factory, replay engine, event bus, Apify, auth
│   │   ├── worker/                 # Background worker entrypoint
│   │   └── main.py                 # FastAPI app entrypoint (routers, middleware, lifespan/auto-seed)
│   ├── alembic/                     # DB migrations
│   ├── scripts/                      # Warehouse builder + user seeding
│   └── tests/                         # pytest suite (agents, auth, RBAC, automation)
├── frontend/
│   ├── src/
│   │   ├── OrchestratorView.tsx, OrdersAgentDashboard.tsx, InventoryAgentView.tsx,
│   │   │   CustomerAgentView.tsx, DomainAgentView.tsx   # per-agent dashboards
│   │   ├── ApprovalsView.tsx, AgentReportingControls.tsx, IngestionControlBar.tsx,
│   │   │   CompetitorFeedControl.tsx                    # automation, reporting & data-control panels
│   │   ├── components/CesiumRouteViewer.tsx              # 3D logistics route map
│   │   ├── auth/AuthContext.tsx, pages/Login.tsx          # auth flow
│   │   └── lib/{api,ws,format,markdown,reportGenerator}.ts # API/WS clients & helpers
│   └── package.json
├── infra/aws/
│   ├── bootstrap/              # one-time: tfstate backend + GitHub OIDC role
│   ├── modules/                # network, ecr, datasets, secrets, rds, redis, alb, ecs, observability
│   └── envs/{dev,prod}/        # per-environment Terraform root modules
├── .github/workflows/
│   ├── ci.yml                  # lint, typecheck, tests, image build + Trivy scan
│   ├── deploy.yml               # build+push -> migrate -> roll ECS forward -> smoke test
│   └── infra.yml                 # Terraform plan (PR) / apply (manual, prod approval-gated)
├── docs/adr/                  # Architecture decision records
├── run_backend.py             # Uvicorn backend runner
├── start_backend.bat          # 1-click Windows backend launcher
├── start_frontend.bat         # 1-click Windows frontend launcher
├── Makefile                   # setup/dev/test/lint/deploy shortcuts
└── docker-compose.yml         # Containerized full-stack definition
```
