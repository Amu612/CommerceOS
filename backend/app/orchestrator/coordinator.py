"""
Nexus Orchestrator — runs every domain agent, normalises their output into a
common snapshot, correlates findings across domains, resolves conflicting
recommendations deterministically, and produces one coordinated view.

It is NOT a business agent — it owns no domain logic; it coordinates the six.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.database.session import SessionLocal
from app.orchestrator.schemas import (
    Conflict,
    DomainFinding,
    DomainSnapshot,
    OrchestrationResult,
    SystemicFinding,
)
from app.services.llm import get_llm

logger = get_logger("orchestrator")

_HEALTH_RANK = {"CRITICAL": 4, "ERROR": 4, "NEEDS_ATTENTION": 3, "NOT_ESTIMABLE": 2, "HEALTHY": 1}
_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

_DISPLAY = {
    "orders": "Orders",
    "inventory": "Inventory",
    "customer": "Customer Support",
    "logistics": "Logistics",
    "pricing": "Pricing & Margin",
    "marketing": "Marketing",
}


class NexusOrchestrator:
    def run(self, db: Session | None = None) -> OrchestrationResult:
        close = db is None
        exec_id = f"EXEC-ORCH-{uuid.uuid4().hex[:6].upper()}"
        now = datetime.now(timezone.utc).isoformat()

        # Each domain runs in its own session (thread-safe) via a factory.
        tasks: Dict[str, Callable[[], DomainSnapshot]] = {
            "orders": self._orders,
            "inventory": self._inventory,
            "logistics": self._logistics,
            "pricing": self._pricing,
            "marketing": self._marketing,
            "customer": self._customer,
        }

        snapshots: List[DomainSnapshot] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._safe, name, fn): name for name, fn in tasks.items()}
            for fut in futures:
                snapshots.append(fut.result())
        snapshots.sort(key=lambda s: -_HEALTH_RANK.get(s.health, 0))

        systemic = self._correlate(snapshots)
        conflicts = self._resolve_conflicts(snapshots)
        kpis = self._kpis(snapshots)

        worst = max((_HEALTH_RANK.get(s.health, 0) for s in snapshots), default=1)
        overall_health = next((h for h, r in _HEALTH_RANK.items() if r == worst), "HEALTHY")
        confidences = [s.confidence for s in snapshots if s.confidence > 0]
        overall_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

        priority = self._priority_actions(snapshots, systemic)
        summary, llm_backed = self._summary(snapshots, systemic, conflicts, overall_health)

        if close:
            pass  # per-domain sessions already closed

        return OrchestrationResult(
            execution_id=exec_id,
            timestamp=now,
            overall_health=overall_health,
            overall_confidence=overall_conf,
            summary=summary,
            domains=snapshots,
            systemic_findings=systemic,
            conflicts=conflicts,
            priority_actions=priority,
            kpis=kpis,
            llm_backed=llm_backed,
        )

    # ── per-domain adapters ─────────────────────────────────────
    def _safe(self, name: str, fn: Callable[[], DomainSnapshot]) -> DomainSnapshot:
        start = time.perf_counter()
        try:
            snap = fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("orchestrator_domain_failed", agent=name, error=str(exc))
            snap = DomainSnapshot(agent=name, display_name=_DISPLAY.get(name, name.title()),
                                  health="ERROR", headline=f"Agent failed: {exc}", error=str(exc))
        snap.latency_ms = round((time.perf_counter() - start) * 1000, 1)
        return snap

    def _orders(self) -> DomainSnapshot:
        from app.agents.orders import orders_agent

        db = SessionLocal()
        try:
            o = orders_agent.run_analysis(db=db, generate_notifications=False)
            findings = [
                DomainFinding(agent="orders", category=f.category, severity=f.severity,
                              title=f.what_happened[:120], recommended_action=f.recommended_action, confidence=f.confidence)
                for f in o.findings[:5]
            ]
            return DomainSnapshot(
                agent="orders", display_name="Orders",
                health={"HEALTHY": "HEALTHY", "NEEDS_ATTENTION": "NEEDS_ATTENTION", "CRITICAL": "CRITICAL"}.get(o.health.status, "NOT_ESTIMABLE"),
                headline=o.health.summary_message, confidence=o.confidence,
                metrics=[
                    {"label": "Total Orders", "value": o.summary.total_orders},
                    {"label": "Fulfillment", "value": f"{o.summary.fulfillment_rate_pct}%"},
                    {"label": "Cancellation", "value": f"{o.summary.cancellation_rate_pct}%"},
                    {"label": "Pending Backlog", "value": o.summary.pending_orders},
                ],
                findings=findings,
            )
        finally:
            db.close()

    def _inventory(self) -> DomainSnapshot:
        from app.agents.inventory import inventory_agent

        db = SessionLocal()
        try:
            r = inventory_agent.run_monitor(db=db)
            low = len(r.low_stock_products or [])
            total = r.metrics.total_products if r.metrics else 0
            health = "CRITICAL" if any(a.severity == "CRITICAL" for a in (r.alerts or [])) else ("NEEDS_ATTENTION" if low else "HEALTHY")
            findings = [
                DomainFinding(agent="inventory", category="STOCK", severity=a.severity or "MEDIUM",
                              title=(a.message or "")[:120], recommended_action=a.reason or "Review reorder plan.", confidence=0.7,
                              evidence=f"product_id={a.product_id} sku={a.sku} name={a.name}")
                for a in (r.alerts or [])[:5]
            ]
            return DomainSnapshot(
                agent="inventory", display_name="Inventory", health=health,
                headline=f"{low} low-stock item(s) across {total:,} products; {len(r.recommendations or [])} reorder candidate(s).",
                confidence=0.75,
                metrics=[
                    {"label": "Products", "value": total},
                    {"label": "Low Stock", "value": low},
                    {"label": "Reorder Candidates", "value": len(r.recommendations or [])},
                    {"label": "Inventory Value", "value": f"R${(r.metrics.inventory_value if r.metrics else 0):,.0f}"},
                ],
                findings=findings,
            )
        finally:
            db.close()

    def _logistics(self) -> DomainSnapshot:
        from app.agents.logistics import logistics_agent

        db = SessionLocal()
        try:
            return self._from_common(logistics_agent.run_analysis(db=db))
        finally:
            db.close()

    def _pricing(self) -> DomainSnapshot:
        from app.agents.pricing import pricing_agent

        db = SessionLocal()
        try:
            return self._from_common(pricing_agent.run_analysis(db=db))
        finally:
            db.close()

    def _marketing(self) -> DomainSnapshot:
        from app.agents.marketing import marketing_agent

        db = SessionLocal()
        try:
            return self._from_common(marketing_agent.run_analysis(db=db))
        finally:
            db.close()

    def _customer(self) -> DomainSnapshot:
        # Customer agent is interactive; surface a light readiness snapshot.
        from app.agents.customer import customer_support_agent

        manifest = customer_support_agent.manifest()
        return DomainSnapshot(
            agent="customer", display_name="Customer Support", health="HEALTHY",
            headline=f"{len(manifest)} support agents ready (triage → router → specialists → supervisor). Interactive channel.",
            confidence=0.6,
            metrics=[{"label": "Specialist Agents", "value": len(manifest)}, {"label": "Mode", "value": "Interactive"}],
        )

    @staticmethod
    def _from_common(out: Any) -> DomainSnapshot:
        return DomainSnapshot(
            agent=out.agent, display_name=_DISPLAY.get(out.agent, out.agent.title()),
            health=out.health, headline=out.summary, confidence=out.confidence,
            metrics=[m.model_dump() for m in out.metrics],
            findings=[
                DomainFinding(agent=out.agent, category=f.category, severity=f.severity,
                              title=f.title, recommended_action=f.recommended_action, confidence=f.confidence)
                for f in out.findings[:5]
            ],
        )

    # ── cross-domain reasoning ──────────────────────────────────
    def _correlate(self, snaps: List[DomainSnapshot]) -> List[SystemicFinding]:
        by = {s.agent: s for s in snaps}
        cats = {s.agent: {f.category for f in s.findings} for s in snaps}
        out: List[SystemicFinding] = []

        def has(agent, *needles):
            return any(any(n in c for n in needles) for c in cats.get(agent, set()))

        # Backlog + late delivery + churn → systemic fulfilment crisis
        if has("orders", "BACKLOG", "FULFILLMENT") and has("logistics", "SLA", "LATE", "CARRIER", "LANE"):
            domains = ["orders", "logistics"]
            if has("customer", "") or by.get("customer", DomainSnapshot(agent="c", display_name="c")).health != "HEALTHY":
                domains.append("customer")
            out.append(SystemicFinding(
                title="Orders and deliveries are running late together",
                severity="HIGH",
                domains=domains,
                explanation="Unshipped orders and delivery delays are happening at the same time. When packages move slowly, more buyers call support or cancel orders.",
                recommended_action="Speed up the oldest orders on the slowest routes. Give realistic delivery dates and text buyers before they have to ask.",
            ))

        # Cancellation + loss-making orders + discount leakage → margin-negative demand
        if has("orders", "CANCELLATION") and has("pricing", "LOSS_MAKING", "DISCOUNT", "SEGMENT_MARGIN"):
            out.append(SystemicFinding(
                title="Big discounts are causing money losses",
                severity="MEDIUM",
                domains=["orders", "pricing", "marketing"],
                explanation="Many orders with heavy discounts are losing money or getting cancelled. Big sales are bringing in buyers who quickly cancel or buy below cost.",
                recommended_action="Stop giving discounts that are too big. Make sure every sale makes a basic profit, and send deals to repeat buyers instead.",
            ))

        # Inventory low stock + marketing demand concentration → stockout risk on the hero category
        if has("inventory", "STOCK") and has("marketing", "DEMAND_CONCENTRATION"):
            out.append(SystemicFinding(
                title="Top selling items are running out of stock",
                severity="HIGH",
                domains=["inventory", "marketing"],
                explanation="Most sales come from one main category, but warehouse stock for it is very low. If it runs out, sales will drop sharply.",
                recommended_action="Order more items for this top category right away. Pause big ad spending on it until new stock arrives.",
            ))

        return out

    def _resolve_conflicts(self, snaps: List[DomainSnapshot]) -> List[Conflict]:
        cats = {s.agent: {f.category for f in s.findings} for s in snaps}
        conflicts: List[Conflict] = []

        pricing_wants_markdown = any("LOSS_MAKING" in c or "DISCOUNT" in c or "SEGMENT_MARGIN" in c for c in cats.get("pricing", set()))
        inventory_low = any("STOCK" in c for c in cats.get("inventory", set()))
        if pricing_wants_markdown and inventory_low:
            conflicts.append(Conflict(
                between=["pricing", "inventory"],
                description="Pricing wants to discount/clear slow or low-margin stock; Inventory is short and a markdown would accelerate a stockout.",
                resolution="Inventory constraint wins for low-stock SKUs: no markdown while cover is below the reorder point. Apply Pricing's margin floor only to well-stocked SKUs.",
            ))

        marketing_wants_scale = any("DEMAND_CONCENTRATION" in c for c in cats.get("marketing", set()))
        orders_backlog = any("BACKLOG" in c or "FULFILLMENT" in c for c in cats.get("orders", set()))
        if marketing_wants_scale and orders_backlog:
            conflicts.append(Conflict(
                between=["marketing", "orders"],
                description="Marketing wants to scale acquisition on the hero category; Orders has a fulfilment backlog that more volume would worsen.",
                resolution="Delay the acquisition push until the backlog clears below its empirical fence; in the interim run retention campaigns (no new fulfilment load).",
            ))

        return conflicts

    def _kpis(self, snaps: List[DomainSnapshot]) -> Dict[str, Any]:
        kpi: Dict[str, Any] = {}
        for s in snaps:
            for m in s.metrics:
                kpi[f"{s.agent}.{m['label']}"] = m["value"]
        kpi["domains_healthy"] = sum(1 for s in snaps if s.health == "HEALTHY")
        kpi["domains_total"] = len(snaps)
        kpi["open_findings"] = sum(len(s.findings) for s in snaps)
        kpi["critical_findings"] = sum(1 for s in snaps for f in s.findings if f.severity == "CRITICAL")
        return kpi

    def _priority_actions(self, snaps: List[DomainSnapshot], systemic: List[SystemicFinding]) -> List[str]:
        actions: List[str] = [f"[SYSTEMIC] {sf.recommended_action}" for sf in systemic if sf.severity in ("HIGH", "CRITICAL")]
        ranked = sorted(
            (f for s in snaps for f in s.findings),
            key=lambda f: (-_SEV_RANK.get(f.severity, 0), -f.confidence),
        )
        for f in ranked[:5]:
            actions.append(f"[{f.agent.upper()} · {f.severity}] {f.recommended_action}")
        return actions[:8]

    def _summary(self, snaps, systemic, conflicts, overall_health) -> tuple[str, bool]:
        healthy = sum(1 for s in snaps if s.health == "HEALTHY")
        base = (
            f"Cross-domain sweep of {len(snaps)} agents — overall {overall_health.replace('_', ' ').title()}. "
            f"{healthy}/{len(snaps)} domains healthy, {sum(len(s.findings) for s in snaps)} open findings, "
            f"{len(systemic)} systemic pattern(s), {len(conflicts)} conflict(s) resolved."
        )
        llm = get_llm()
        if llm.available():
            ctx = {
                "domains": [{"agent": s.agent, "health": s.health, "headline": s.headline} for s in snaps],
                "systemic": [sf.model_dump() for sf in systemic],
                "conflicts": [c.model_dump() for c in conflicts],
            }
            import json

            try:
                txt = llm.generate_text(
                    system="You are the Nexus Orchestrator. Write a 3-4 sentence briefing for the operations team. "
                    "Use very simple, clear everyday words that anyone can easily understand. "
                    "Do NOT use complex vocabulary, high-level English, or business jargon. "
                    "Cover overall health, the biggest issue, and the main action to take. "
                    "Use ONLY the data provided.\n\n" + json.dumps(ctx, default=str)[:4000],
                    user="Give me the cross-domain briefing in simple language.",
                    max_tokens=280,
                    untrusted=False,
                )
            except Exception as exc:  # noqa: BLE001 — LLM down / rate-limited: degrade, never 502 the sweep
                logger.warning("orchestrator_briefing_failed", error=str(exc))
                txt = ""
            if txt:
                return txt, True
        return base, False


orchestrator = NexusOrchestrator()
