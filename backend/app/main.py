"""
CommerceOS — FastAPI application entrypoint.

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
    except Exception as exc:
        logger.warning("replay_index_failed", error=str(exc))

    # Auto-seed an empty database from the bundled dataset (dev convenience).
    db = SessionLocal()
    try:
        from app.models.dataco import DataCoOrder
        from app.models.olist import Order

        order_count = db.query(Order).count()
        dataco_count = db.query(DataCoOrder).count()
        # Check both sources independently — a partial prior seed (e.g. the
        # process was killed mid-import, or Olist loaded but the DataCo CSV
        # was briefly missing) must not look "seeded" just because Olist has
        # rows; seed_data() re-runs both loaders, and each is dedup-safe on a
        # rerun (checks existing ids first), so calling it again here never
        # duplicates whichever source already succeeded.
        if order_count == 0 or dataco_count == 0:
            logger.info("db_empty_seeding", olist_orders=order_count, dataco_orders=dataco_count)
            try:
                from scripts.seed_nexus_data import seed_data

                seed_data(db=db, seed_orders=True)
                logger.info("db_seed_complete")
            except Exception as exc:
                logger.warning("db_seed_failed", error=str(exc))

        # `replay_engine`'s ingest-progress counters are in-memory only, so a
        # process restart forgets how far a prior replay got even though the
        # data it already streamed is still sitting in the database — the
        # Ingestion Control Bar would misleadingly read "0 orders / stopped"
        # right after restarting a backend that has been fully seeded for
        # weeks. Resync it to what's actually in the database: `complete_now`
        # is dedup-safe (every insert already checks for an existing id), so
        # this is a cheap catch-up, never a re-import. Re-query fresh rather
        # than reusing order_count/dataco_count — seeding above may have just
        # changed one or both from 0.
        has_data = db.query(Order).count() > 0 or db.query(DataCoOrder).count() > 0
        if has_data:
            try:
                msg = replay_engine.complete_now()
                logger.info("replay_synced_to_existing_data", message=msg)
            except Exception as exc:
                logger.warning("replay_sync_failed", error=str(exc))
    finally:
        db.close()

    # Ensure default admin and seeded role accounts exist and credentials match on startup
    try:
        from scripts.seed_users import seed_users

        seed_users()
        logger.info("users_seed_verified_on_startup")
    except Exception as exc:
        logger.warning("users_seed_on_startup_failed", error=str(exc))

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

from app.api.customer_routes import alias_router as customer_alias_router, customer_router  # noqa: E402
from app.api.inventory_routes import alias_router as inventory_alias_router, inventory_router  # noqa: E402
from app.api.routes import ingestion_router, nexus_sim_router, orders_router, simulation_router  # noqa: E402
from app.api.v1 import audit as v1_audit, auth as v1_auth, users as v1_users  # noqa: E402
from app.api.v1.deps import (  # noqa: E402
    agent_dependency,
    auth_dependency,
    get_current_user,
    ingestion_dependency,
    orchestrator_dependency,
)

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

from app.api.v1 import (  # noqa: E402
    automation as v1_automation,
    orchestrator as v1_orchestrator,
    runs as v1_runs,
    stream as v1_stream,
    system as v1_system,
)
from app.api.v1.agents import logistics_router, marketing_router, pricing_router  # noqa: E402

# RBAC — see app.core.rbac for the single source of truth these dependencies
# read from. Each domain agent's router is gated by its own admin role: an
# ORDERS_ADMIN gets 403 from every Inventory/Pricing/etc. route, and so on.
# The Orchestrator sweeps all six agents at once and cannot be scoped to one
# domain, so it's SUPER_ADMIN-only. Automation/Approvals and run history stay
# on the generic "any authenticated user" gate at the router level — they
# self-scope per-request instead (list_approvals/list_actions/list_runs
# filter to the caller's own agent; approve/reject 403 on a foreign-role
# approval) because a domain admin legitimately does need to decide their
# own domain's approvals, just not anyone else's.
for r, agent in (
    (orders_router, "orders"),
    (inventory_router, "inventory"),
    (inventory_alias_router, "inventory"),
    (customer_router, "customer"),
    (customer_alias_router, "customer"),
    (logistics_router, "logistics"),
    (marketing_router, "marketing"),
    (pricing_router, "pricing"),
):
    app.include_router(r, dependencies=[Depends(agent_dependency(agent))])

app.include_router(v1_orchestrator.router, dependencies=[Depends(orchestrator_dependency())])

# Ingestion/replay control — SUPER_ADMIN-only, applied identically to all
# three URL aliases (see `ingestion_dependency`).
_ingestion_auth = [Depends(ingestion_dependency())]
for r in (ingestion_router, simulation_router, nexus_sim_router):
    app.include_router(r, dependencies=_ingestion_auth)

for r in (
    v1_automation.router,
    v1_runs.router,
):
    app.include_router(r, dependencies=_agent_auth)

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
