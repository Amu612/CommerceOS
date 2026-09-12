"""
Database engine + session factories.

Reads all config from `app.core.settings`. Supports a primary (read/write) URL
and an optional read-only URL (RDS read replica / restricted role) used by the
analytics agent and heavy dashboards.

Migrations are NOT run here — `alembic upgrade head` is an explicit deploy step
(container entrypoint / one-shot task). `init_db()` remains only as a dev/test
convenience for SQLite.
"""
from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("db")


def _make_engine(url: str, *, readonly: bool = False):
    if url.startswith("sqlite"):
        # `check_same_thread=False` lets FastAPI's threadpool share the file;
        # `timeout=30` makes sqlite3 itself retry for 30s (instead of failing
        # almost instantly) when another connection briefly holds the write
        # lock — the two together are what actually eliminate the
        # "database is locked" failures agents/automation used to surface as
        # a hard FAILED status under any concurrent load (orchestrator run +
        # websocket stream + an approval decision, all writing at once).
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            echo=settings.DB_ECHO,
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            # WAL: readers no longer block writers (or vice versa) — the
            # single biggest lever against "database is locked" with several
            # agents/the replay engine/automation hitting one SQLite file.
            cur.execute("PRAGMA journal_mode=WAL")
            # NORMAL is safe under WAL (durable across app crashes; only an
            # OS crash could lose the last commit) and notably faster than
            # the FULL default — part of what was making requests slow.
            cur.execute("PRAGMA synchronous=NORMAL")
            # Belt-and-suspenders on top of connect_args timeout: have SQLite
            # itself wait up to 30s for a lock before raising, instead of the
            # ~5s default.
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(
        url,
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=settings.DB_POOL_RECYCLE_SECONDS,
        connect_args={"options": "-c default_transaction_read_only=on"} if readonly else {},
    )


engine = _make_engine(settings.DATABASE_URL)
read_engine = (
    engine
    if settings.effective_read_url == settings.DATABASE_URL
    else _make_engine(settings.effective_read_url, readonly=True)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
ReadSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=read_engine)

# Backwards-compat constant referenced by older modules/scripts.
DATABASE_URL = settings.DATABASE_URL


def get_db():
    """FastAPI dependency: primary read/write session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db():
    """FastAPI dependency: read-only session (replica / restricted role)."""
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db() -> bool:
    """Liveness check used by /health/ready."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("db_check_failed", error=str(exc))
        return False


def init_db() -> None:
    """
    Ensure the schema exists.
    - SQLite (dev/test): create_all from models.
    - Postgres: no-op — the schema is owned by `alembic upgrade head` (deploy step).
    """
    if settings.db_is_sqlite:
        from app.models import Base

        Base.metadata.create_all(bind=engine)
