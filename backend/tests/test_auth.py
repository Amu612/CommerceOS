import pytest
from fastapi.testclient import TestClient

from app.api.v1.deps import get_current_user, get_optional_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database.session import SessionLocal
from app.main import app
from app.models.security import User, UserRole
from app.services.auth_service import create_user, get_user_by_username


# ── unit: hashing + jwt ──────────────────────────────────────────

def test_password_hash_roundtrip():
    h = hash_password("s3cret-passw0rd")
    assert h != "s3cret-passw0rd"
    assert verify_password("s3cret-passw0rd", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip_and_tamper():
    tok = create_access_token("u1", role="SUPER_ADMIN", username="admin")
    payload = decode_token(tok)
    assert payload["sub"] == "u1"
    assert payload["type"] == "access"
    assert decode_token(tok + "x") is None
    assert decode_token("not.a.jwt") is None


def test_refresh_token_type():
    assert decode_token(create_refresh_token("u1"))["type"] == "refresh"


# ── integration: real auth (override removed) ────────────────────

@pytest.fixture
def unauth_client():
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_optional_user, None)
    yield TestClient(app)
    app.dependency_overrides.update(saved)


@pytest.fixture(scope="module")
def seeded_users():
    db = SessionLocal()
    try:
        if not get_user_by_username(db, "t_admin"):
            create_user(db, username="t_admin", email="t_admin@x.local", password="AdminPass123!", role=UserRole.SUPER_ADMIN)
        if not get_user_by_username(db, "t_orders"):
            create_user(db, username="t_orders", email="t_orders@x.local", password="OrdersPass123!", role=UserRole.ORDERS_ADMIN)
    finally:
        db.close()


def test_login_success_and_me(unauth_client, seeded_users):
    r = unauth_client.post("/api/v1/auth/login", json={"username": "t_admin", "password": "AdminPass123!"})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    me = unauth_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200
    assert me.json()["role"] == "SUPER_ADMIN"


def test_login_bad_password(unauth_client, seeded_users):
    r = unauth_client.post("/api/v1/auth/login", json={"username": "t_admin", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"


def test_protected_route_requires_token(unauth_client):
    assert unauth_client.get("/api/orders/health").status_code == 401
    assert unauth_client.post("/api/orders/query", json={"message": "hi"}).status_code == 401


def test_rbac_users_route_forbidden_for_non_admin(unauth_client, seeded_users):
    r = unauth_client.post("/api/v1/auth/login", json={"username": "t_orders", "password": "OrdersPass123!"})
    tok = r.json()["access_token"]
    resp = unauth_client.get("/api/v1/users", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 403


def test_health_is_public(unauth_client):
    assert unauth_client.get("/health").status_code == 200
