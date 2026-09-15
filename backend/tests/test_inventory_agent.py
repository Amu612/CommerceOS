from app.agents.inventory import inventory_agent
from app.agents.inventory.schemas import InventoryAgentResponse
from app.agents.inventory.tools import InventoryTools
from app.database.session import SessionLocal


def test_01_inventory_tools():
    from conftest import require_olist_data

    require_olist_data()
    db = SessionLocal()
    try:
        prods = InventoryTools.query_products(db, threshold=50, limit=10)
        assert len(prods) > 0
        assert prods[0].product_id is not None
        assert prods[0].stockQuantity is not None
        assert prods[0].price is not None

        # Reorder recs
        recs = InventoryTools.get_reorder_recommendations(db, threshold=50, limit=5)
        assert len(recs) > 0
        assert recs[0].reorder_point is not None
        assert recs[0].suggested_quantity is not None

        # Sales trends
        trends = InventoryTools.analyze_sales_trends(db, days=30, limit=5)
        assert len(trends) > 0
        assert trends[0].total_sales is not None

        # Metrics
        metrics = InventoryTools.get_inventory_metrics(db, threshold=50)
        assert metrics.total_products > 0
        assert metrics.low_stock_count >= 0
    finally:
        db.close()


def test_02_inventory_agent_monitor():
    from conftest import require_olist_data

    require_olist_data()
    db = SessionLocal()
    try:
        res: InventoryAgentResponse = inventory_agent.run_monitor(db=db, threshold=50)
        assert res.status == "SUCCESS"
        assert len(res.products) > 0
        assert len(res.recommendations) > 0
        assert len(res.alerts) > 0
        assert res.metrics.total_products > 0
        assert res.output is not None
    finally:
        db.close()


def test_03_inventory_agent_query():
    """With no LLM configured the query falls back to the database-backed
    monitoring snapshot; with an LLM it answers through the ReAct loop.
    Either way the response contract must hold and the answer be non-empty."""
    db = SessionLocal()
    try:
        res = inventory_agent.query("What products are low on stock?", db=db)
        assert res.status == "SUCCESS"
        assert res.output is not None and len(res.output) > 0
        assert res.products is not None
        assert res.low_stock_products is not None
    finally:
        db.close()
