<div align="center">

# ⚡ CommerceOS 
### Autonomous Multi-Agent Operating System for Intelligent E-Commerce Operations

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB.svg?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.6-3178C6.svg?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-ReAct%20Agents-FF4B4B.svg?style=for-the-badge)](https://langchain-ai.github.io/langgraph/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

<p align="center">
  <b>CommerceOS</b> replaces fragmented, manual e-commerce administration with autonomous, specialized AI agents. Operating over real transaction and supply chain data, it continuously detects anomalies, optimizes inventory replenishment, accelerates order fulfillment, and autonomously mitigates operational risks.
</p>

[Key Features](#-key-features) • [Architecture](#-system-architecture) • [Getting Started](#-getting-started) • [Default Credentials](#-default-credentials) • [API Reference](#-api-reference) • [Tech Stack](#-tech-stack)

---

</div>

## 🌟 Key Features

### 📦 Orders Operations Intelligence Agent
- **Empirical Pipeline Auditing**: Real-time evaluation of delay rates, fulfillment ratios, and backlog aging across 13,000+ orders.
- **Autonomous Query Triage**: Identifies Order IDs, Product SKUs, Customer IDs, and natural language analytics queries with instant database entity verification.
- **Milestone Shipment Tracking**: Multi-carrier event timeline synthesis (`[ORDER_PLACED]` → `[IN_TRANSIT]` → `[DELIVERED]`).
- **Automated RMA & Returns**: Algorithmic 30-day policy enforcement and RMA authorization without false positives.

### 🛡️ Smart Inventory Watchdog Agent
- **Continuous Stock Monitoring**: 24/7 autonomous guardian evaluating inventory across 11,000+ products.
- **Predictive Replenishment**: Dynamic **Reorder Point (ROP = Lead Time Demand + Safety Stock)** and **Economic Order Quantity (EOQ)** batch estimation.
- **Sales Velocity Profiling**: 30-day moving window velocity analysis and demand trajectory classification (`INCREASING`, `STABLE`, `DECREASING`).
- **Proactive Anomaly Alerts**: Real-time dispatch of `CRITICAL`, `HIGH`, and `MEDIUM` priority stockout risk notifications.

### 🤖 Multi-Provider LLM Resilience
- **Automated Failover Architecture**:
  1. Local Gemini Web2API proxy (`http://localhost:8081/v1`)
  2. Google Gemini REST API (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-pro`)
  3. OpenAI API (`gpt-4o-mini`, `gpt-4o`)
  4. Groq Cloud Engine (`llama-3.1-8b-instant`)
  5. Local Ollama (`llama3.2`)
- **Deterministic Natural Language Synthesizer**: Fallback conversational engine that formats data into structured, actionable insights even when external cloud APIs are unreachable.

---

## 🚀 Getting Started

### Prerequisites
- **Python 3.11+**
- **Node.js 18+** and **npm**
- *(Optional)* **Docker & Docker Compose**

---

### Option 1: Quick Start on Windows (Recommended)

1. **Start the Backend**:
   Double-click `start_backend.bat` (or run in terminal):
   ```powershell
   python run_backend.py
   ```
   - Server running at: **`http://127.0.0.1:8000`**
   - Interactive Swagger API docs: **`http://127.0.0.1:8000/docs`**

2. **Start the Frontend**:
   Double-click `start_frontend.bat` (or run in terminal):
   ```powershell
   cd frontend
   npm install
   npm run dev
   ```
   - Dashboard running at: **`http://localhost:3000`**

---

### Option 2: Docker Compose (Full Stack)

Run the full production stack including PostgreSQL, Redis, backend, workers, and frontend:

```bash
docker compose up --build
```

---

## 🔐 Default Credentials

The platform is pre-seeded with dedicated role-based access control (RBAC) accounts:

| Username | Role | Assigned Permissions | Default Password |
| :--- | :--- | :--- | :--- |
| **`admin`** | **Super Admin** | Full platform authority across all agents, users, and audit logs | `CommerceOS2026!` |
| **`orders_admin`** | **Orders Admin** | Orders intelligence, backlog oversight, SLA metrics, and returns | `CommerceOS2026!` |
| **`inventory_admin`** | **Inventory Admin** | Stock watchdog, catalog valuation, ROP, and purchase orders | `CommerceOS2026!` |
| **`support_admin`** | **Support Admin** | Customer inquiries, order dispute resolutions, and RMAs | `CommerceOS2026!` |
| **`pricing_admin`** | **Pricing Admin** | Catalog pricing benchmarks and margin optimization | `CommerceOS2026!` |
| **`logistics_admin`** | **Logistics Admin** | Dispatch carrier tracking and milestone management | `CommerceOS2026!` |
| **`marketing_admin`** | **Marketing Admin** | Demand forecasting and promotion velocity analysis | `CommerceOS2026!` |

---

## 📡 API Reference

Interactive OpenAPI documentation is generated at **`/docs`** or **`/redoc`**. Key endpoints:

### Orders Intelligence
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/orders/analyze` | Triggers a full empirical pipeline analysis cycle |
| `POST` | `/api/orders/query` | Interactive natural language / ReAct tool query |
| `GET` | `/api/orders/latest` | Retrieves cached analysis snapshot |
| `GET` | `/api/orders/health` | Pipeline health scorecard and active anomalies |

### Smart Inventory Watchdog
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET / POST` | `/api/v1/agents/inventory/monitor` | Runs watchdog cycle (stock, alerts, ROP, valuation) |
| `POST` | `/api/v1/agents/inventory/query` | Conversational query for inventory status and trends |
| `POST` | `/api/v1/agents/inventory/reorder` | Triggers restocking purchase order |

### Authentication & System
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/v1/auth/login` | Issues JWT bearer token |
| `GET` | `/api/v1/system/health` | Comprehensive infrastructure and database healthcheck |

---

## 🛠️ Tech Stack

- **Backend Framework**: [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic v2](https://docs.pydantic.dev/)
- **Agent Orchestration**: [LangGraph](https://langchain-ai.github.io/langgraph/), [LangChain Core](https://python.langchain.com/)
- **Database & ORM**: [SQLAlchemy 2.0](https://www.sqlalchemy.org/), [Alembic](https://alembic.sqlalchemy.org/), PostgreSQL / SQLite
- **Statistical Intelligence**: Custom Modified Z-Score, IQR, MAD, and Percentile distribution profilers
- **Frontend Architecture**: [React 18](https://react.dev/), [TypeScript](https://www.typescriptlang.org/), [Vite](https://vitejs.dev/)
- **DevOps & Cloud**: Docker, Docker Compose, Terraform (AWS ECS Fargate, RDS, ElastiCache, S3, CloudFront)

---

## 📁 Repository Structure

```text
├── backend/
│   ├── app/
│   │   ├── agents/            # Domain agents (orders, inventory, customer, etc.)
│   │   ├── api/               # FastAPI routers, middleware, and dependencies
│   │   ├── core/              # Security, settings, and structured logging
│   │   ├── database/          # SQLAlchemy session factories and initialization
│   │   ├── intelligence/      # Anomaly detectors, statistics, and confidence scoring
│   │   ├── models/            # SQLAlchemy database models
│   │   └── services/          # Multi-provider LLM service, replay engine, event bus
│   ├── scripts/               # Warehouse builders and user seeding scripts
│   └── requirements.txt       # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── pages/             # Login, dashboard, and agent views
│   │   ├── App.tsx            # Main application root and navigation
│   │   └── config.ts          # API endpoints configuration
│   └── package.json           # Frontend dependencies
├── infra/                     # AWS Terraform infrastructure modules
├── start_backend.bat          # 1-click Windows backend launcher
├── start_frontend.bat         # 1-click Windows frontend launcher
├── run_backend.py             # Uvicorn backend runner
└── docker-compose.yml         # Containerized full-stack definition
```
