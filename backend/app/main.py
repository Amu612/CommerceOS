"""
CommerceOS / Nexus — FastAPI application entrypoint.

M1 wires the production foundation (settings, structured logging, request
context, typed error handling, security middleware) while keeping every existing
route working. API consolidation under /api/v1 happens in milestone M7.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.middleware.audit import AuditMiddleware
from app.api.middleware.request_context import RequestContextMiddleware, add_request_id_header
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.core.logging import configure_logging, get_logger
from app.core.settings import settings

configure_logging()
logger = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", environment=settings.ENVIRONMENT, version=settings.VERSION)

    from app.database.session import SessionLocal, init_db

    init_db()

    # Index dataset events so replay start/pause/step is instant.
    try:
        from app.services.replay_engine import replay_engine

        indexed = replay_engine.index_events_from_datasets(max_orders=settings.REPLAY_MAX_ORDERS)
        logger.info("replay_indexed", events=indexed)
    except Exception as exc:  # noqa: BLE001 - startup best-effort
        logger.warning("replay_index_failed", error=str(exc))

    # Auto-seed an empty database from the bundled dataset (dev convenience).
    db = SessionLocal()
    try:
        from app.models.olist import Order

        if db.query(Order).count() == 0:
            logger.info("db_empty_seeding")
            try:
                from scripts.seed_nexus_data import seed_data

                seed_data(db=db)
                logger.info("db_seed_complete")
            except Exception as exc:  # noqa: BLE001
                logger.warning("db_seed_failed", error=str(exc))
    finally:
        db.close()

    yield
    logger.info("shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Autonomous multi-agent operating system for e-commerce operations.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.EXPOSE_DOCS else None,
    redoc_url="/redoc" if settings.EXPOSE_DOCS else None,
    openapi_url="/openapi.json" if settings.EXPOSE_DOCS else None,
)

# ── Middleware (added last = runs first) ──────────────────────────
app.middleware("http")(add_request_id_header)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
)
if settings.TRUSTED_HOSTS != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)

register_error_handlers(app)

# ── Routers ──────────────────────────────────────────────────────
from fastapi import Depends  # noqa: E402

from app.api.customer_routes import alias_router as customer_alias_router  # noqa: E402
from app.api.customer_routes import customer_router  # noqa: E402
from app.api.inventory_routes import alias_router as inventory_alias_router  # noqa: E402
from app.api.inventory_routes import inventory_router  # noqa: E402
from app.api.routes import nexus_sim_router, orders_router, simulation_router  # noqa: E402
from app.api.v1 import audit as v1_audit  # noqa: E402
from app.api.v1 import auth as v1_auth  # noqa: E402
from app.api.v1 import users as v1_users  # noqa: E402
from app.api.v1.deps import auth_dependency, get_current_user  # noqa: E402

# Public v1 (auth flows) — no dependency
app.include_router(v1_auth.router, prefix=settings.API_V1_PREFIX)
# Admin v1 — always strict
_admin = [Depends(get_current_user)]
app.include_router(v1_users.router, prefix=settings.API_V1_PREFIX, dependencies=_admin)
app.include_router(v1_audit.router, prefix=settings.API_V1_PREFIX, dependencies=_admin)

# Agent + orchestrator + simulation routers.
# Auth is enforced when settings.AUTH_ENFORCED (always in production); soft in dev
# so the demo dashboard works without a login wall.
_agent_auth = [Depends(auth_dependency())]

from app.api.v1 import automation as v1_automation  # noqa: E402
from app.api.v1 import data_source as v1_data_source  # noqa: E402
from app.api.v1 import orchestrator as v1_orchestrator  # noqa: E402
from app.api.v1 import runs as v1_runs  # noqa: E402
from app.api.v1 import shopify as v1_shopify  # noqa: E402
from app.api.v1 import stream as v1_stream  # noqa: E402
from app.api.v1 import system as v1_system  # noqa: E402
from app.api.v1.agents import (  # noqa: E402
    logistics_router,
    marketing_router,
    pricing_router,
)

for r in (
    orders_router,
    inventory_router,
    inventory_alias_router,
    customer_router,
    customer_alias_router,
    simulation_router,
    nexus_sim_router,
    logistics_router,
    marketing_router,
    pricing_router,
    v1_orchestrator.router,
    v1_automation.router,
    v1_runs.router,
    v1_data_source.router,
    v1_shopify.router,
):
    app.include_router(r, dependencies=_agent_auth)

# Shopify webhook — Shopify calls this directly and can never carry our JWT;
# its security is the HMAC signature check inside the route itself, not auth.
app.include_router(v1_shopify.webhook_router)

# System status / LLM health — public so the frontend can discover auth mode
# and provider health before a session exists.
app.include_router(v1_system.router)

# WebSocket stream — auth handled inside the endpoint (query token).
app.include_router(v1_stream.router)


# ── Health ───────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "service": settings.PROJECT_NAME, "version": settings.VERSION}


@app.get("/health/ready", tags=["Health"])
def health_ready():
    from app.database.session import check_db

    db_ok = check_db()
    status = "ok" if db_ok else "degraded"
    code = 200 if db_ok else 503
    from starlette.responses import JSONResponse

    return JSONResponse(
        status_code=code,
        content={"status": status, "checks": {"database": db_ok}},
    )


@app.get("/", tags=["Health"])
def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "status": "ONLINE",
        "docs": "/docs" if settings.EXPOSE_DOCS else None,
    }
