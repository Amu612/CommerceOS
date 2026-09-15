import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.agents.orders import DataCategory, OrdersAgent, OrdersAgentOutput, orders_agent
from app.database.session import SessionLocal, init_db
from app.main import app
from app.models import Base, Order
from app.services.replay_engine import replay_engine


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    init_db()
    db = SessionLocal()
    try:
        cnt = db.query(Order).count()
        if cnt == 0:
            replay_engine.step(100)
    finally:
        db.close()


@pytest.fixture
def memory_db():
    """Isolated empty in-memory SQLite session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_01_imports_and_types():
    assert orders_agent is not None
    assert isinstance(orders_agent, OrdersAgent)
    assert orders_agent.agent_name == "orders"
    assert OrdersAgentOutput is not None
    assert DataCategory.OBSERVED == "OBSERVED"
    assert DataCategory.CALCULATED == "CALCULATED"
    assert DataCategory.UNAVAILABLE == "UNAVAILABLE"


def test_02_empty_database_returns_insufficient_data(memory_db):
    orders_agent.reset(db=memory_db)
    out = orders_agent.run_analysis(db=memory_db, generate_notifications=False)
    assert out.health.status in ["NOT_ESTIMABLE", "INSUFFICIENT_DATA"]
    assert out.summary.total_orders == 0
    assert out.confidence == 0.0
    assert len(out.findings) == 0
    assert out.forecast is None


def test_03_analysis_on_nexus_database():
    """Runs analysis on the live seeded orders.db."""
    db = SessionLocal()
    try:
        out = orders_agent.run_analysis(db=db, generate_notifications=False)
        assert out.summary.total_orders > 0
        assert out.summary.fulfillment_rate_pct >= 0.0
        assert out.health.status in ["HEALTHY", "NEEDS_ATTENTION", "CRITICAL"]
        assert out.pending_queue is not None
        assert out.cancellation_risk is not None
        assert out.fulfillment_health is not None
        assert out.investigation_summary is not None
        assert len(out.investigation_summary.tools_executed) > 0
    finally:
        db.close()


def test_04_queue_aging_percentiles():
    db = SessionLocal()
    try:
        out = orders_agent.run_analysis(db=db, generate_notifications=False)
        pq = out.pending_queue
        if pq.pending_count > 0 and pq.p90_age_hours is not None:
            if pq.p75_age_hours is not None:
                assert pq.p75_age_hours <= pq.p90_age_hours
            if pq.p95_age_hours is not None:
                assert pq.p90_age_hours <= pq.p95_age_hours
            if pq.max_age_hours is not None:
                assert pq.p90_age_hours <= pq.max_age_hours
    finally:
        db.close()


def test_05_interactive_query_general():
    res = orders_agent.query("Hello assistant")
    assert res.success is True
    assert res.intent in ("react", "deterministic")
    assert res.result is not None and len(res.result) > 0


def test_06_interactive_query_return_policy():
    res = orders_agent.query("What is your refund and return policy?")
    assert res.success is True
    assert res.intent in ("react", "deterministic")
    assert "Return Window" in res.result or "policy" in res.result.lower()


def test_07_fastapi_analyze_endpoint():
    client = TestClient(app)
    resp = client.post("/api/orders/analyze", json={"generate_notifications": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "orders"
    assert "summary" in data
    assert "health" in data
    assert "pending_queue" in data
    assert "cancellation_risk" in data
    assert "fulfillment_health" in data
    assert "findings" in data


def test_08_fastapi_query_endpoint():
    client = TestClient(app)
    resp = client.post("/api/orders/query", json={"message": "check return policy"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["intent"] in ("react", "deterministic")
    assert len(data["result"]) > 0


def test_09_fastapi_health_endpoint():
    client = TestClient(app)
    resp = client.get("/api/orders/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "summary" in data


def test_10_ingestion_endpoints_status_and_control():
    client = TestClient(app)

    # 1. Check status
    res = client.get("/api/orders/ingestion/status")
    assert res.status_code == 200
    status_data = res.json()
    assert "status" in status_data
    assert "speed" in status_data
    assert "events_processed" in status_data

    # 2. Step 25 records into system
    step_res = client.post("/api/orders/ingestion/control", json={"action": "step", "step": 25})
    assert step_res.status_code == 200
    step_data = step_res.json()
    assert step_data["status"] == "success"
    assert step_data["action"] == "step"

    # 3. Pause
    pause_res = client.post("/api/orders/ingestion/control", json={"action": "pause"})
    assert pause_res.status_code == 200
    pause_data = pause_res.json()
    assert pause_data["status"] == "success"

    # 4. Analyze on the current slice
    ana_res = client.post("/api/orders/analyze", json={"generate_notifications": False})
    assert ana_res.status_code == 200
    ana_data = ana_res.json()
    assert ana_data["summary"]["total_orders"] > 0


def test_11_simulation_endpoints_nexus_compatibility():
    client = TestClient(app)

    # Nexus-compatible /api/simulation endpoints
    sim_status = client.get("/api/simulation/status")
    assert sim_status.status_code == 200
    assert "status" in sim_status.json()

    # Nexus-compatible /dashboard/simulation endpoints
    dash_status = client.get("/dashboard/simulation/status")
    assert dash_status.status_code == 200
    assert "status" in dash_status.json()


def test_12_processing_metrics_dynamically_populated():
    """Verify processing distribution and fulfillment metrics are never null when data exists."""
    db = SessionLocal()
    try:
        out = orders_agent.run_analysis(db=db)
        fh = out.fulfillment_health
        assert fh is not None
        assert fh.avg_processing_hours is not None
        assert fh.median_processing_hours is not None
        assert fh.p90_processing_hours is not None
        assert fh.avg_processing_hours > 0
        assert fh.p90_processing_hours >= fh.median_processing_hours
    finally:
        db.close()


def test_13_query_product_and_order_intelligence():
    """Verify natural language queries work for order lookup, product search, and pipeline analytics."""
    client = TestClient(app)

    # 1. Product lookup by product ID
    prod_res = client.post(
        "/api/orders/query", json={"message": "show me product id 1e9e8ef04dbcff4541ed26657ea517e5"}
    )
    assert prod_res.status_code == 200
    p_data = prod_res.json()
    assert p_data["success"] is True
    assert p_data["intent"] in ("react", "deterministic")
    assert (
        "Perfumaria" in p_data["result"]
        or "1e9e8ef04dbcff4541ed26657ea517e5" in p_data["result"]
        or "product" in p_data["result"].lower()
    )

    # 2. Order lookup
    ord_res = client.post("/api/orders/query", json={"message": "order 58"})
    assert ord_res.status_code == 200
    o_data = ord_res.json()
    assert o_data["success"] is True
    assert o_data["intent"] in ("react", "deterministic")
    assert "58" in o_data["result"]

    # 3. Pipeline analytics
    ana_res = client.post("/api/orders/query", json={"message": "what is the delay rate and SLA status?"})
    assert ana_res.status_code == 200
    a_data = ana_res.json()
    assert a_data["success"] is True
    assert a_data["intent"] in ("react", "deterministic")
    assert len(a_data["result"]) > 0
