"""
Central application settings.

Every configurable value in the backend flows through here. No module outside
`app.core.settings` should read `os.environ` directly.

Precedence: process env vars > `.env` file > defaults below.
Select the profile with `ENVIRONMENT=development|production|test`.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _backend_dir() -> str:
    # backend/app/core/settings/base.py -> backend/
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _project_root() -> str:
    return os.path.dirname(_backend_dir())


# Absolute path to backend/.env so config is found regardless of the process CWD
# (uvicorn --reload / gunicorn / pytest all run from different directories).
_ENV_FILE = os.getenv("ENV_FILE") or os.path.join(_backend_dir(), ".env")


class BaseAppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # `.env.example` (and a copy of it as `.env`) ships every key blank as
        # a template — without this, a blank value overrides a field's real
        # default with an empty string (e.g. DATABASE_URL="") instead of
        # falling through to it.
        env_ignore_empty=True,
    )

    # ── Identity ───────────────────────────────────────────────────
    PROJECT_NAME: str = "CommerceOS AI"
    VERSION: str = "2.0.0"
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    API_V1_PREFIX: str = "/api/v1"

    # ── Server ─────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 2

    # ── Database ───────────────────────────────────────────────────
    # Cloud/compose: postgresql://user:pass@host:5432/commerceos
    # Local fallback (pre-M2): sqlite file at repo root.
    DATABASE_URL: str = f"sqlite:///{os.path.join(_project_root(), 'orders.db').replace(os.sep, '/')}"
    # Read replica / read-only role. Falls back to DATABASE_URL if unset.
    DATABASE_READ_URL: Optional[str] = None
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE_SECONDS: int = 1800
    DB_ECHO: bool = False

    # ── Redis ──────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False  # flipped on in compose/cloud

    # ── Auth / security ────────────────────────────────────────────
    JWT_SECRET: str = "dev-only-insecure-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_TTL_MINUTES: int = 60 * 12
    JWT_REFRESH_TTL_MINUTES: int = 60 * 24 * 7
    SEED_ADMIN_PASSWORD: str = "CommerceOS2026!"
    PASSWORD_MIN_LENGTH: int = 10
    # When False, read/query agent routes are open (dev convenience). Always True in prod.
    AUTH_ENFORCED: bool = False

    # NoDecode: these come from a plain comma-separated env string, not JSON —
    # without it pydantic-settings tries to json.loads() the raw value before
    # our _split_csv validator ever runs, and blows up on a real .env file.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"])
    TRUSTED_HOSTS: Annotated[List[str], NoDecode] = Field(default_factory=lambda: ["*"])
    MAX_REQUEST_BYTES: int = 2 * 1024 * 1024
    RATE_LIMIT_PER_MINUTE: int = 240
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10
    LOGIN_MAX_FAILURES: int = 5
    EXPOSE_DOCS: bool = True

    # ── LLM provider ───────────────────────────────────────────────
    # auto | deterministic | groq | openai | anthropic | bedrock
    # "auto" picks the first provider that has a key (groq > openai > anthropic > bedrock),
    # else deterministic.
    LLM_PROVIDER: Literal["auto", "deterministic", "groq", "openai", "anthropic", "bedrock"] = "auto"
    LLM_MODEL: str = ""  # blank = provider default
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    LLM_TIMEOUT_SECONDS: float = 20.0
    LLM_MAX_RETRIES: int = 2
    LLM_REQUEST_TOKEN_BUDGET: int = 6000
    LLM_MONTHLY_BUDGET_USD: float = 200.0
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-latest"
    BEDROCK_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    GROQ_API_KEY: Optional[str] = None
    GROQ_API_BASE: str = "https://api.groq.com/openai/v1"
    # Legacy keys still honoured by the deterministic->real bridge during migration.
    GEMINI_API_KEY: Optional[str] = None
    # OpenAI-compatible local proxy (e.g. gemini web2api). If set, used automatically.
    GEMINI_WEB2API_BASE_URL: Optional[str] = None
    GEMINI_WEB2API_API_KEY: Optional[str] = None
    GEMINI_WEB2API_MODEL: Optional[str] = None

    # ── Data / ingestion ───────────────────────────────────────────
    DATASET_DIR: Optional[str] = None  # local dir OR s3://bucket/prefix
    REPLAY_MAX_ORDERS: int = 15000

    # ── TomTom (Logistics Route Intelligence) ──────────────────────
    TOMTOM_API_KEY: Optional[str] = None  # set via env; never hardcode keys in source

    # ── Apify (competitor price feed — Pricing agent only) ──────────
    APIFY_TOKEN: Optional[str] = None
    APIFY_ACTOR_ID: Optional[str] = None
    PRICING_COMPETITOR_FEED_ENABLED: bool = False

    # ── Observability ──────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: Optional[str] = None
    METRICS_ENABLED: bool = True

    # ── Derived ────────────────────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_test(self) -> bool:
        return self.ENVIRONMENT == "test"

    @property
    def effective_read_url(self) -> str:
        return self.DATABASE_READ_URL or self.DATABASE_URL

    @property
    def db_is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def apify_configured(self) -> bool:
        return bool(self.APIFY_TOKEN and self.APIFY_ACTOR_ID)

    def resolve_llm(self) -> tuple[str, str]:
        """Return (provider, model) — resolving 'auto' to whatever has credentials."""
        provider = self.LLM_PROVIDER
        if provider == "auto":
            if self.GROQ_API_KEY:
                provider = "groq"
            elif self.OPENAI_API_KEY or self.GEMINI_WEB2API_API_KEY:
                provider = "openai"
            elif self.ANTHROPIC_API_KEY:
                provider = "anthropic"
            else:
                provider = "deterministic"
        model = self.LLM_MODEL or {
            "groq": self.GROQ_MODEL,
            "openai": self.OPENAI_MODEL,
            "anthropic": self.ANTHROPIC_MODEL,
            "bedrock": self.BEDROCK_MODEL_ID,
        }.get(provider, "")
        return provider, model

    @field_validator("CORS_ORIGINS", "TRUSTED_HOSTS", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


class DevAppSettings(BaseAppSettings):
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    DB_ECHO: bool = False
    EXPOSE_DOCS: bool = True


class TestAppSettings(BaseAppSettings):
    ENVIRONMENT: Literal["development", "production", "test"] = "test"
    LLM_PROVIDER: Literal["deterministic", "openai", "anthropic", "bedrock"] = "deterministic"
    REDIS_ENABLED: bool = False
    LOG_LEVEL: str = "WARNING"
    AUTH_ENFORCED: bool = True  # tests verify the secured path; conftest overrides the dep


class ProdAppSettings(BaseAppSettings):
    ENVIRONMENT: Literal["development", "production", "test"] = "production"
    EXPOSE_DOCS: bool = False
    REDIS_ENABLED: bool = True
    LOG_JSON: bool = True
    OTEL_ENABLED: bool = True
    AUTH_ENFORCED: bool = True

    @field_validator("JWT_SECRET")
    @classmethod
    def _reject_default_secret(cls, v: str) -> str:
        if v == "dev-only-insecure-secret-change-me":
            raise ValueError("JWT_SECRET must be set to a real value in production")
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_postgres(cls, v: str) -> str:
        if v.startswith("sqlite"):
            raise ValueError("DATABASE_URL must point to PostgreSQL in production")
        return v


@lru_cache
def get_settings() -> BaseAppSettings:
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env == "production":
        return ProdAppSettings()
    if env == "test":
        return TestAppSettings()
    return DevAppSettings()
