import pytest
from fastapi.testclient import TestClient

from app.database.session import SessionLocal
from app.agents.customer import customer_support_agent, CustomerAgentResponse
from app.agents.customer.tools import extract_order_id, CustomerSupportTools
from app.main import app

client = TestClient(app)


def _sample_order_id(db):
    from app.models.olist import Order
    row = db.query(Order.order_id).filter(Order.order_status == "delivered").first()
    return row[0] if row else None


def test_01_manifest():
    m = customer_support_agent.manifest()
    ids = {a["id"] for a in m}
    assert {"triage", "router", "support", "sales", "billing", "refund", "supervisor"} <= ids


def test_02_extract_order_id():
    assert extract_order_id("where is order #58?") == "58"
    assert extract_order_id("refund for e481f51cbdc54678b7cc49136f2d6af7") == "e481f51cbdc54678b7cc49136f2d6af7"
    assert extract_order_id("hello there") == ""


def test_03_triage_and_pipeline():
    r = customer_support_agent.query("I want a refund")
    assert isinstance(r, CustomerAgentResponse)
    assert r.category == "refund"
    assert "refund" in r.agents_involved
    assert "triage" in r.agents_involved and "supervisor" in r.agents_involved
    assert r.final_response


def test_04_order_grounded_answer():
    db = SessionLocal()
    try:
        oid = _sample_order_id(db)
        assert oid is not None
        r = customer_support_agent.query(f"where is my order {oid}?")
        assert r.category == "support"
        assert r.order_context is not None
        assert r.order_context["order_id"].startswith(oid[:8])
        assert any(t.name in ("lookup_order", "track_shipment") for t in r.tool_calls)
    finally:
        db.close()


def test_05_billing_uses_db():
    db = SessionLocal()
    try:
        from app.models.dataco import DataCoOrder
        from app.models.olist import Order

        oid = db.query(DataCoOrder.order_id).first() or db.query(Order.order_id).first()
        assert oid is not None, "no orders seeded"
        text, recs = CustomerSupportTools.billing_lookup(str(oid[0]), db=db)
        assert "R$" in text  # store currency is BRL (R$) — Olist/DataCo is Brazilian
    finally:
        db.close()


def test_06_api_endpoints():
    res = client.get("/api/customer/agents")
    assert res.status_code == 200
    assert len(res.json()["agents"]) >= 7

    res = client.post("/api/customer/query", json={"query": "do you sell perfumaria products?"})
    assert res.status_code == 200
    body = res.json()
    assert body["category"] == "sales"
    assert body["final_response"]

    # alias route
    assert client.get("/api/v1/agents/customer/agents").status_code == 200


def test_07_stream_endpoint():
    with client.stream("GET", "/api/customer/stream", params={"query": "hello"}) as res:
        assert res.status_code == 200
        chunks = "".join(res.iter_text())
    assert '"type": "meta"' in chunks
    assert '"type": "done"' in chunks
