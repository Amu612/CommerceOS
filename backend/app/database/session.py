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

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("db")


def _make_engine(url: str, *, readonly: bool = False):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, echo=settings.DB_ECHO)
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
