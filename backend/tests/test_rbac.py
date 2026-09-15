"""
RBAC end-to-end test suite.

Exercises the real HTTP stack — login, JWT issuance, and every protected
route's dependency chain — rather than importing `app.core.rbac`'s functions
and calling them directly, because the thing that actually has to be correct
is what the deployed server does with a real request, not that the helper
functions return the right booleans in isolation.

Covers, per the brief: login, authorized access, unauthorized cross-agent
access (all 6 domain roles x all 6 agents), direct route access without a
token, the Orchestrator (SUPER_ADMIN-only), Approvals (scoped, not
all-or-nothing), and admin-only functionality (user management, audit log).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.v1.deps import get_current_user, get_optional_user
from app.automation import Mode, propose_for_run
from app.database.session import SessionLocal
from app.main import app
from app.models.security import UserRole
from app.services.auth_service import create_user

# ── Fixtures: real users with known passwords, real JWTs, no dependency
#    overrides — this is the actual auth stack, not a stand-in for it.
# ─────────────────────────────────────────────────────────────────────

ROLES = [
    ("orders", UserRole.ORDERS_ADMIN),
    ("inventory", UserRole.INVENTORY_ADMIN),
    ("customer", UserRole.CUSTOMER_SUPPORT_ADMIN),
    ("pricing", UserRole.PRICING_ADMIN),
    ("marketing", UserRole.MARKETING_ADMIN),
    ("logistics", UserRole.LOGISTICS_ADMIN),
]
ALL_AGENTS = [agent for agent, _ in ROLES]
PASSWORD = "Test-Password-123!"

# One lightweight, always-available GET/POST per agent — RBAC denies at the
# router's dependency, before the route body runs, so which endpoint is used
# doesn't matter for these checks as long as it belongs to that agent.
AGENT_PROBE = {
    "orders": ("POST", "/api/orders/analyze", {}),
    "inventory": ("POST", "/api/v1/agents/inventory/monitor", {}),
    "customer": ("GET", "/api/v1/agents/customer/agents", None),
    "pricing": ("POST", "/api/v1/agents/pricing/analyze", {}),
    "marketing": ("POST", "/api/v1/agents/marketing/analyze", {}),
    "logistics": ("POST", "/api/v1/agents/logistics/analyze", {}),
}

ORCHESTRATOR_PROBE = ("GET", "/api/v1/orchestrator/latest")


def _probe(client: TestClient, agent: str, headers: dict) -> int:
    method, path, body = AGENT_PROBE[agent]
    if method == "GET":
        return client.get(path, headers=headers).status_code
    return client.post(path, json=body, headers=headers).status_code


@pytest.fixture(scope="module")
def rbac_users() -> dict[str, str]:
    """One real user per role + super admin, each with a real password and a
    real JWT obtained through the actual /auth/login route below (not
    fabricated) — created once per module run, unique usernames so reruns
    against the shared dev DB never collide. Returns just the usernames
    (plain strings, not ORM objects) — the User rows are committed and this
    session closes immediately after, so any lazy attribute access on the
    ORM instances themselves would raise DetachedInstanceError.
    """
    db = SessionLocal()
    usernames: dict[str, str] = {}
    try:
        suffix = uuid.uuid4().hex[:8]
        for agent, role in ROLES:
            username = f"rbac_test_{agent}_{suffix}"
            create_user(db, username=username, email=f"{username}@test.local", password=PASSWORD, role=role)
            usernames[agent] = username
        username = f"rbac_test_super_{suffix}"
        create_user(
            db,
            username=username,
            email=f"{username}@test.local",
            password=PASSWORD,
            role=UserRole.SUPER_ADMIN,
        )
        usernames["super"] = username
    finally:
        db.close()
    return usernames


@pytest.fixture
def real_auth_client(rbac_users):
    """A TestClient with the session-wide `get_current_user`/`get_optional_user`
    override (see conftest.py's autouse `_override_auth`, which makes every
    other test implicitly SUPER_ADMIN) removed — requests here go through the
    real JWT-decode-and-look-up-the-user dependency, exactly like production.
    Restores the override afterward so the rest of the suite is unaffected.
    """
    had_current = app.dependency_overrides.pop(get_current_user, None)
    had_optional = app.dependency_overrides.pop(get_optional_user, None)
    try:
        yield TestClient(app)
    finally:
        if had_current is not None:
            app.dependency_overrides[get_current_user] = had_current
        if had_optional is not None:
            app.dependency_overrides[get_optional_user] = had_optional


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 1. Login ─────────────────────────────────────────────────────────


def test_login_succeeds_and_returns_permitted_agents(real_auth_client, rbac_users):
    body = _login(real_auth_client, rbac_users["orders"])
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["role"] == "ORDERS_ADMIN"
    assert body["user"]["permitted_agents"] == ["orders"]
    assert body["user"]["can_access_orchestrator"] is False
    assert body["user"]["is_super_admin"] is False


def test_login_wrong_password_is_rejected(real_auth_client, rbac_users):
    r = real_auth_client.post(
        "/api/v1/auth/login",
        json={"username": rbac_users["orders"], "password": "wrong-password"},
    )
    assert r.status_code == 401


def test_super_admin_login_reports_all_agents_and_orchestrator(real_auth_client, rbac_users):
    body = _login(real_auth_client, rbac_users["super"])
    assert body["user"]["is_super_admin"] is True
    assert body["user"]["can_access_orchestrator"] is True
    assert set(body["user"]["permitted_agents"]) == set(ALL_AGENTS)


# ── 2. Direct route access without a token ──────────────────────────


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_agent_route_requires_authentication(real_auth_client, agent):
    assert _probe(real_auth_client, agent, {}) == 401


def test_orchestrator_requires_authentication(real_auth_client):
    method, path = ORCHESTRATOR_PROBE
    assert real_auth_client.get(path).status_code == 401


def test_admin_routes_require_authentication(real_auth_client):
    assert real_auth_client.get("/api/v1/users").status_code == 401
    assert real_auth_client.get("/api/v1/audit").status_code == 401


def test_invalid_token_is_rejected(real_auth_client):
    assert _probe(real_auth_client, "orders", _auth_headers("not-a-real-token")) == 401


# ── 3. Authorized access: each domain admin on their OWN agent ─────


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_domain_admin_can_access_own_agent(real_auth_client, rbac_users, agent):
    token = _login(real_auth_client, rbac_users[agent])["access_token"]
    status = _probe(real_auth_client, agent, _auth_headers(token))
    assert status == 200, f"{agent}_admin should reach its own agent, got {status}"


# ── 4. Unauthorized cross-agent access: 403 on every OTHER agent ───


@pytest.mark.parametrize("owner_agent", ALL_AGENTS)
def test_domain_admin_gets_403_on_every_other_agent(real_auth_client, rbac_users, owner_agent):
    token = _login(real_auth_client, rbac_users[owner_agent])["access_token"]
    headers = _auth_headers(token)
    for other_agent in ALL_AGENTS:
        if other_agent == owner_agent:
            continue
        status = _probe(real_auth_client, other_agent, headers)
        assert status == 403, f"{owner_agent}_admin should be denied '{other_agent}', got {status}"


def test_super_admin_can_access_every_agent(real_auth_client, rbac_users):
    token = _login(real_auth_client, rbac_users["super"])["access_token"]
    headers = _auth_headers(token)
    for agent in ALL_AGENTS:
        assert _probe(real_auth_client, agent, headers) == 200


# ── 5. Orchestrator: SUPER_ADMIN only ────────────────────────────────


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_domain_admin_gets_403_on_orchestrator(real_auth_client, rbac_users, agent):
    token = _login(real_auth_client, rbac_users[agent])["access_token"]
    _, path = ORCHESTRATOR_PROBE
    r = real_auth_client.get(path, headers=_auth_headers(token))
    assert r.status_code == 403


def test_super_admin_can_access_orchestrator(real_auth_client, rbac_users):
    token = _login(real_auth_client, rbac_users["super"])["access_token"]
    _, path = ORCHESTRATOR_PROBE
    r = real_auth_client.get(path, headers=_auth_headers(token))
    assert r.status_code == 200


# ── 5b. Ingestion/replay control: SUPER_ADMIN only, on all 3 URL aliases ──
# Regression coverage for a real gap this RBAC pass found: the /api/orders/...
# path inherited Orders' agent-scoped gate, but the /api/simulation/... and
# /dashboard/simulation/... aliases over the exact same handler sat on the
# generic "any authenticated user" check — any domain admin could reset the
# whole platform's dataset through those two even though the "front door"
# path correctly refused everyone but orders_admin/SUPER_ADMIN.

INGESTION_STATUS_PATHS = [
    "/api/orders/ingestion/status",
    "/api/simulation/status",
    "/dashboard/simulation/status",
]


@pytest.mark.parametrize("agent", ALL_AGENTS)
@pytest.mark.parametrize("path", INGESTION_STATUS_PATHS)
def test_domain_admin_gets_403_on_ingestion_control_every_alias(real_auth_client, rbac_users, agent, path):
    token = _login(real_auth_client, rbac_users[agent])["access_token"]
    r = real_auth_client.get(path, headers=_auth_headers(token))
    assert r.status_code == 403, f"{agent}_admin should be denied {path}, got {r.status_code}"


@pytest.mark.parametrize("path", INGESTION_STATUS_PATHS)
def test_super_admin_can_access_ingestion_control_every_alias(real_auth_client, rbac_users, path):
    token = _login(real_auth_client, rbac_users["super"])["access_token"]
    r = real_auth_client.get(path, headers=_auth_headers(token))
    assert r.status_code == 200


# ── 6. Admin-only functionality: user management + audit log ───────


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_domain_admin_gets_403_on_admin_routes(real_auth_client, rbac_users, agent):
    token = _login(real_auth_client, rbac_users[agent])["access_token"]
    headers = _auth_headers(token)
    assert real_auth_client.get("/api/v1/users", headers=headers).status_code == 403
    assert real_auth_client.get("/api/v1/audit", headers=headers).status_code == 403


def test_super_admin_can_access_admin_routes(real_auth_client, rbac_users):
    token = _login(real_auth_client, rbac_users["super"])["access_token"]
    headers = _auth_headers(token)
    assert real_auth_client.get("/api/v1/users", headers=headers).status_code == 200
    assert real_auth_client.get("/api/v1/audit", headers=headers).status_code == 200


# ── 7. Approvals: scoped to the requester's own domain, not all-or-nothing ──


def test_approval_flow_is_scoped_to_the_owning_domain(real_auth_client, rbac_users):
    """A finding that requires LOGISTICS_ADMIN approval must be visible to and
    decidable by the logistics_admin test user, invisible to a different
    domain admin's PENDING list, and refused (403) if that other domain admin
    tries to decide it directly by id — the exact "unauthorized ... approval"
    boundary the RBAC brief calls out."""
    from app.models.operations import Approval

    db = SessionLocal()
    try:
        title = f"rbac-test finding {uuid.uuid4().hex[:8]}"
        created = propose_for_run(
            db,
            agent="logistics",
            findings=[
                {
                    "category": "LATE_DELIVERY_RISK",  # -> FLAG_FOR_REVIEW
                    "severity": "CRITICAL",  # forces NEEDS_APPROVAL regardless of action type
                    "confidence": 0.9,
                    "title": title,
                    "recommended_action": "test",
                    "evidence": "carrier=TestCarrier",
                }
            ],
        )
        assert created and created[0].mode == Mode.NEEDS_APPROVAL.value
        action_id = created[0].id
        approval = db.query(Approval).filter(Approval.action_id == action_id).first()
        assert approval is not None, "CRITICAL finding should have required manual approval"
        approval_id = approval.id
    finally:
        db.close()

    logistics_token = _login(real_auth_client, rbac_users["logistics"])["access_token"]
    marketing_token = _login(real_auth_client, rbac_users["marketing"])["access_token"]

    # The owning domain admin sees it in their pending queue.
    r = real_auth_client.get(
        "/api/v1/automation/approvals?status=PENDING", headers=_auth_headers(logistics_token)
    )
    assert r.status_code == 200
    assert any(item["id"] == action_id for item in r.json()["items"])

    # A different domain admin does NOT see it in theirs.
    r = real_auth_client.get(
        "/api/v1/automation/approvals?status=PENDING", headers=_auth_headers(marketing_token)
    )
    assert r.status_code == 200
    assert all(item["id"] != action_id for item in r.json()["items"])

    # ...and is refused outright if they try to decide it directly by id.
    r = real_auth_client.post(
        f"/api/v1/automation/approvals/{approval_id}/approve",
        json={"reason": "should not be allowed"},
        headers=_auth_headers(marketing_token),
    )
    assert r.status_code == 403

    # The owning domain admin can approve it.
    r = real_auth_client.post(
        f"/api/v1/automation/approvals/{approval_id}/approve",
        json={"reason": "approved by owning domain"},
        headers=_auth_headers(logistics_token),
    )
    assert r.status_code == 200
    assert r.json()["status"] in ("VERIFIED", "EXECUTED")


def test_automation_action_history_is_scoped_to_own_agent(real_auth_client, rbac_users):
    """`/api/v1/automation/actions` must not leak another domain's automation
    history to a domain admin, and must 403 an explicit cross-domain query."""
    orders_token = _login(real_auth_client, rbac_users["orders"])["access_token"]

    r = real_auth_client.get("/api/v1/automation/actions", headers=_auth_headers(orders_token))
    assert r.status_code == 200
    assert all(item["agent"] == "orders" for item in r.json()["items"])

    r = real_auth_client.get(
        "/api/v1/automation/actions?agent=marketing", headers=_auth_headers(orders_token)
    )
    assert r.status_code == 403


def test_run_history_is_scoped_to_own_agent(real_auth_client, rbac_users):
    pricing_token = _login(real_auth_client, rbac_users["pricing"])["access_token"]

    r = real_auth_client.get("/api/v1/runs", headers=_auth_headers(pricing_token))
    assert r.status_code == 200
    assert all(item["agent"] == "pricing" for item in r.json()["items"])

    r = real_auth_client.get("/api/v1/runs?agent=inventory", headers=_auth_headers(pricing_token))
    assert r.status_code == 403


# ── 8. Logout ────────────────────────────────────────────────────────


def test_logout_is_recorded_and_succeeds(real_auth_client, rbac_users):
    token = _login(real_auth_client, rbac_users["orders"])["access_token"]
    r = real_auth_client.post("/api/v1/auth/logout", headers=_auth_headers(token))
    assert r.status_code == 200
