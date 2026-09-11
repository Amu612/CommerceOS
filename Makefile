.DEFAULT_GOAL := help
SHELL := /bin/bash
PY ?= python
BACKEND := backend
FRONTEND := frontend

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Setup ────────────────────────────────────────────────
.PHONY: setup
setup: ## Install backend + frontend deps
	cd $(BACKEND) && $(PY) -m pip install -r requirements-dev.txt
	cd $(FRONTEND) && npm ci

# ── Local run ────────────────────────────────────────────
.PHONY: dev
dev: ## Run backend (reload) + frontend dev servers (needs 2 terminals normally)
	cd $(BACKEND) && $(PY) -m uvicorn app.main:app --reload --port 8000

.PHONY: up
up: ## docker compose up (postgres, redis, backend, worker, frontend)
	docker compose up -d --build

.PHONY: down
down: ## docker compose down
	docker compose down

.PHONY: logs
logs: ## Tail compose logs
	docker compose logs -f --tail=100

# ── Database ─────────────────────────────────────────────
.PHONY: migrate
migrate: ## Apply DB migrations
	cd $(BACKEND) && alembic upgrade head

.PHONY: migration
migration: ## Create a new migration: make migration m="add x"
	cd $(BACKEND) && alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Build the warehouse + seed users
	cd $(BACKEND) && $(PY) -m scripts.build_warehouse && $(PY) -m scripts.seed_users

# ── Quality ──────────────────────────────────────────────
.PHONY: fmt
fmt: ## Format code
	cd $(BACKEND) && $(PY) -m ruff check --fix . && $(PY) -m black . && $(PY) -m isort .
	cd $(FRONTEND) && npm run format

.PHONY: lint
lint: ## Lint (no changes)
	cd $(BACKEND) && $(PY) -m ruff check . && $(PY) -m black --check . && $(PY) -m isort --check-only .
	cd $(FRONTEND) && npm run lint

.PHONY: typecheck
typecheck: ## Static type check
	cd $(BACKEND) && $(PY) -m mypy app
	cd $(FRONTEND) && npm run typecheck

.PHONY: test
test: ## Run backend + frontend tests
	cd $(BACKEND) && $(PY) -m pytest -q
	cd $(FRONTEND) && npm run test -- --run

.PHONY: test-cov
test-cov: ## Backend tests with coverage gate
	cd $(BACKEND) && $(PY) -m pytest --cov=app --cov-report=term-missing --cov-report=xml

.PHONY: eval
eval: ## Run the agent eval harness
	cd $(BACKEND) && $(PY) -m eval.run

.PHONY: security
security: ## Dependency + secret scan
	cd $(BACKEND) && $(PY) -m pip_audit -r requirements.txt || true
	cd $(FRONTEND) && npm audit --omit=dev || true
	detect-secrets scan --baseline .secrets.baseline || true

# ── Build / deploy ───────────────────────────────────────
.PHONY: build
build: ## Build container images
	docker build -t commerceos/api:local $(BACKEND)
	docker build -t commerceos/frontend:local $(FRONTEND)

.PHONY: deploy-dev
deploy-dev: ## Terraform apply dev
	cd infra/aws/envs/dev && terraform init && terraform apply

.PHONY: deploy-prod
deploy-prod: ## Terraform apply prod (guarded)
	cd infra/aws/envs/prod && terraform init && terraform plan
