"""
Shared test fixtures.

M1/M3 slice: gives every test an authenticated TestClient by overriding the
auth dependency with a synthetic SUPER_ADMIN. Full ephemeral-Postgres + factory
infrastructure lands in milestone M10/T1.
"""

from __future__ import annotations

import os

# Process env wins over any local backend/.env — keep tests deterministic and
# off the developer's real Postgres.
os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_ENFORCED"] = "true"
os.environ["LLM_PROVIDER"] = "deterministic"
os.environ["REDIS_ENABLED"] = "false"
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "orders.db")
).replace(os.sep, "/")
for _k in ("GEMINI_WEB2API_BASE_URL", "OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_k, None)

import pytest
from fastapi.testclient import TestClient

from app.api.v1.deps import get_current_user, get_optional_user
from app.database.session import SessionLocal, init_db
from app.main import app
from app.models.security import User, UserRole


@pytest.fixture(scope="session", autouse=True)
def _prepare_db():
    init_db()
    # Ensure there is order data for the agent tests (idempotent).
    db = SessionLocal()
    try:
        from app.models.olist import Order

        if db.query(Order).count() == 0:
            from app.services.replay_engine import replay_engine

            replay_engine.step(200)
    finally:
        db.close()


@pytest.fixture(scope="session")
def super_admin() -> User:
    return User(
        id="test-super-admin",
        username="test-admin",
        email="test-admin@commerceos.local",
        hashed_password="x",
        role=UserRole.SUPER_ADMIN,
        is_active=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _override_auth(super_admin: User):
    app.dependency_overrides[get_current_user] = lambda: super_admin
    app.dependency_overrides[get_optional_user] = lambda: super_admin
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_optional_user, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """Alias — the default client is already authenticated in tests."""
    return client
