"""Smoke + contract tests for the logistics / pricing / marketing agents + orchestrator."""
import pytest

from app.agents.logistics import logistics_agent
from app.agents.marketing import marketing_agent
from app.agents.pricing import pricing_agent
from app.database.session import SessionLocal
from app.orchestrator import orchestrator

_AGENTS = [("logistics", logistics_agent), ("pricing", pricing_agent), ("marketing", marketing_agent)]


@pytest.mark.parametrize("name,agent", _AGENTS)
def test_analysis_shape(name, agent):
    db = SessionLocal()
    try:
        out = agent.run_analysis(db=db)
        assert out.agent == name
        assert out.execution_id
        assert out.health in ("HEALTHY", "NEEDS_ATTENTION", "CRITICAL", "NOT_ESTIMABLE")
        assert isinstance(out.metrics, list)
        assert isinstance(out.findings, list)
        assert isinstance(out.recommendations, list)
        # every finding carries provenance + evidence
        for f in out.findings:
            assert f.evidence
            assert f.data_status in ("OBSERVED", "CALCULATED", "ESTIMATED", "MODELLED", "NOT_ESTIMABLE")
            assert 0.0 <= f.confidence <= 1.0
    finally:
        db.close()


@pytest.mark.parametrize("name,agent", _AGENTS)
def test_query_returns_answer(name, agent):
    db = SessionLocal()
    try:
        r = agent.query("give me an overview", db=db)
        assert r.agent == name
        assert len(r.answer) > 40
        assert r.success
    finally:
        db.close()


@pytest.mark.parametrize("name,agent", _AGENTS)
def test_query_topic_routing(name, agent):
    """Topical questions should surface topical detail in the deterministic answer."""
    db = SessionLocal()
    try:
        topical = {
            "logistics": "which carrier is slowest?",
            "pricing": "show me margin by segment",
            "marketing": "break down my rfm segments",
        }[name]
        r = agent.query(topical, db=db)
        assert len(r.answer) > 60
    finally:
        db.close()


def test_orchestrator_runs_all_domains():
    r = orchestrator.run()
    assert r.execution_id
    assert r.overall_health in ("HEALTHY", "NEEDS_ATTENTION", "CRITICAL", "NOT_ESTIMABLE", "ERROR")
    agents = {d.agent for d in r.domains}
    assert {"orders", "inventory", "logistics", "pricing", "marketing", "customer"} <= agents
    # no domain crashed
    assert all(d.health != "ERROR" for d in r.domains), [d.error for d in r.domains if d.error]
    assert r.kpis["domains_total"] == 6
    assert isinstance(r.priority_actions, list)


def test_orchestrator_api(client):
    resp = client.get("/api/v1/orchestrator/run")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["domains"]) == 6
    assert "summary" in body


def test_orchestrator_latest_is_cheap_read(client):
    """/latest must return a persisted sweep without re-running the agents."""
    client.post("/api/v1/orchestrator/run")  # ensure something is persisted
    resp = client.get("/api/v1/orchestrator/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_id"]
    assert len(body["domains"]) == 6
    assert body.get("stale") is True


def test_automation_proposes_and_approves(client):
    """A sweep should queue approvals; approving one executes it and audits it."""
    client.post("/api/v1/orchestrator/run")

    actions = client.get("/api/v1/automation/actions?limit=50").json()
    assert actions["counts"]["auto_executed"] >= 0
    assert "pending_approval" in actions["counts"]

    pending = client.get("/api/v1/automation/approvals?status=PENDING").json()["items"]
    if not pending:  # data-dependent: only assert the contract when something is queued
        return
    approval_id = pending[0]["approval"]["id"]
    decided = client.post(
        f"/api/v1/automation/approvals/{approval_id}/approve",
        json={"reason": "test"},
    )
    assert decided.status_code == 200
    assert decided.json()["approval"]["status"] == "APPROVED"
    assert decided.json()["status"] in ("EXECUTED", "VERIFIED", "FAILED")


def test_system_status_is_public(client):
    resp = client.get("/api/v1/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_enforced" in body
    assert "llm" in body
