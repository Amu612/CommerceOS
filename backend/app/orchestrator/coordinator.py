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
from app.intelligence.confidence.calculator import ConfidenceCalculator
from app.orchestrator.schemas import (
    Conflict,
    DomainFinding,
    DomainSnapshot,
    OrchestrationResult,
    SystemicFinding,
)
from app.services.llm import get_llm

logger = get_logger("orchestrator")

# ERROR is tracked per-domain but must not drag the whole system's verdict to
# CRITICAL — one crashed agent is a degraded system, not proof of a business crisis.
_HEALTH_RANK = {"CRITICAL": 4, "ERROR": 4, "NEEDS_ATTENTION": 3, "NOT_ESTIMABLE": 2, "HEALTHY": 1}
_OVERALL_RANK = {"CRITICAL": 4, "NEEDS_ATTENTION": 3, "NOT_ESTIMABLE": 2, "HEALTHY": 1}
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
    def run(self, db: Session | None = None) -> OrchestrationResult:  # noqa: ARG002 - domains own their sessions
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

        worst = max((_OVERALL_RANK.get(s.health, 0) if s.health != "ERROR" else _OVERALL_RANK["NEEDS_ATTENTION"] for s in snapshots), default=1)
        overall_health = next((h for h, r in _OVERALL_RANK.items() if r == worst), "HEALTHY")
        confidences = [s.confidence for s in snapshots if s.confidence > 0]
        overall_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0

        priority = self._priority_actions(snapshots, systemic)
        summary, llm_backed = self._summary(snapshots, systemic, conflicts, overall_health)

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
            # Confidence scales with how much of the catalogue the sweep actually saw.
            inv_confidence = ConfidenceCalculator.evaluate(sample_size=max(total, 1)).confidence_score
            findings = [
                DomainFinding(agent="inventory", category="STOCK", severity=a.severity or "MEDIUM",
                              title=(a.message or "")[:120], recommended_action=a.reason or "Review reorder plan.",
                              confidence=inv_confidence,
                              evidence=f"product_id={a.product_id} sku={a.sku} name={a.name}")
                for a in (r.alerts or [])[:5]
            ]
            return DomainSnapshot(
                agent="inventory", display_name="Inventory", health=health,
                headline=f"{low} low-stock item(s) across {total:,} products; {len(r.recommendations or [])} reorder candidate(s).",
                confidence=inv_confidence,
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
        # Real, data-driven support-pressure snapshot: the support channel is
        # driven by delayed/late orders and post-delivery issues, so its health
        # is derived from the same live order data the support agents see.
        from app.agents.customer.tools import CustomerSupportTools

        db = SessionLocal()
        try:
            text, _ = CustomerSupportTools.pipeline_snapshot(db=db)
            # pipeline_snapshot -> get_analytics_summary; pull the numbers again
            # directly so we can derive health/confidence from them.
            from app.agents.orders.tools import OrdersTools

            s = OrdersTools.get_analytics_summary("all", db=db)
            total = int(s.get("total_orders", 0) or 0)
            delay_rate = float(s.get("delay_rate_pct", 0.0) or 0.0)
            canc_rate = float(s.get("cancellation_rate_pct", 0.0) or 0.0)
            pressure = delay_rate + canc_rate
            health = "CRITICAL" if pressure >= 30 else ("NEEDS_ATTENTION" if pressure >= 12 else "HEALTHY")
            confidence = ConfidenceCalculator.evaluate(sample_size=max(total, 1)).confidence_score
            return DomainSnapshot(
                agent="customer", display_name="Customer Support",
                health=health,
                headline=text or "Support channel snapshot unavailable.",
                confidence=confidence,
                metrics=[
                    {"label": "Orders In Scope", "value": total},
                    {"label": "Delayed", "value": f"{delay_rate:.1f}%"},
                    {"label": "Cancellations", "value": f"{canc_rate:.1f}%"},
                    {"label": "SLA Health", "value": s.get("sla_health", "UNKNOWN")},
                ],
            )
        except Exception as exc:  # noqa: BLE001 — degrade to a readiness-only snapshot
            logger.warning("customer_snapshot_failed", error=str(exc))
            from app.agents.customer import customer_support_agent

            manifest = customer_support_agent.manifest()
            return DomainSnapshot(
                agent="customer", display_name="Customer Support", health="NOT_ESTIMABLE",
                headline=f"{len(manifest)} support agents ready; no data slice available to derive support pressure.",
                confidence=0.0,
                metrics=[{"label": "Specialist Agents", "value": len(manifest)}, {"label": "Mode", "value": "Interactive"}],
            )
        finally:
            db.close()

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

        def metric(agent, label):
            for m in by.get(agent, DomainSnapshot(agent=agent, display_name=agent)).metrics:
                if m.get("label") == label:
                    return m.get("value")
            return None

        # Backlog + late delivery (+ support pressure) → systemic fulfilment crisis
        if has("orders", "BACKLOG", "FULFILLMENT") and has("logistics", "SLA", "LATE", "CARRIER", "LANE"):
            domains = ["orders", "logistics"]
            # Customer joins only when its data actually shows pressure — never
            # as an unconditional default (the old `"" in category` bug).
            cust_health = by.get("customer").health if by.get("customer") else "HEALTHY"
            if cust_health in ("NEEDS_ATTENTION", "CRITICAL"):
                domains.append("customer")
            fulfil = metric("orders", "Fulfillment")
            backlog = metric("orders", "Pending Backlog")
            out.append(SystemicFinding(
                title="Orders and deliveries are running late together",
                severity="HIGH",
                domains=domains,
                explanation=(
                    f"Unshipped orders (pending backlog: {backlog if backlog is not None else 'n/a'}, "
                    f"fulfillment at {fulfil if fulfil is not None else 'n/a'}) and delivery delays are "
                    "happening at the same time. When packages move slowly, more buyers call support "
                    "or cancel orders."
                ),
                recommended_action=(
                    "Speed up the oldest orders on the slowest routes first"
                    + (" and give the affected buyers realistic delivery dates before they contact support."
                       if "customer" in domains else
                       ", and give buyers realistic delivery dates proactively.")
                ),
            ))

        # Cancellation + loss-making orders + discount leakage → margin-negative demand
        if has("orders", "CANCELLATION") and has("pricing", "LOSS_MAKING", "DISCOUNT", "SEGMENT_MARGIN"):
            canc = metric("orders", "Cancellation")
            out.append(SystemicFinding(
                title="Big discounts are causing money losses",
                severity="MEDIUM",
                domains=["orders", "pricing", "marketing"],
                explanation=(
                    f"Heavily discounted orders are losing money or getting cancelled "
                    f"(cancellation rate: {canc if canc is not None else 'n/a'}). Promotions are "
                    "bringing in buyers who quickly cancel or buy below cost."
                ),
                recommended_action=(
                    "Cap discount depth at the margin floor Pricing measured, and target promotion "
                    "spend at repeat buyers instead of blanket sitewide discounts."
                ),
            ))

        # Inventory low stock + marketing demand concentration → stockout risk on the hero category
        if has("inventory", "STOCK") and has("marketing", "DEMAND_CONCENTRATION"):
            low = metric("inventory", "Low Stock")
            out.append(SystemicFinding(
                title="Top selling items are running out of stock",
                severity="HIGH",
                domains=["inventory", "marketing"],
                explanation=(
                    f"Most sales come from one main category, but warehouse stock for it is low "
                    f"({low if low is not None else 'n/a'} low-stock item(s) flagged). If it runs out, "
                    "sales will drop sharply."
                ),
                recommended_action=(
                    "Expedite replenishment for the top category now and pause paid acquisition on it "
                    "until cover days are back above the reorder point."
                ),
            ))

        return out

    def _resolve_conflicts(self, snaps: List[DomainSnapshot]) -> List[Conflict]:
        by = {s.agent: s for s in snaps}
        cats = {s.agent: {f.category for f in s.findings} for s in snaps}
        conflicts: List[Conflict] = []

        pricing_wants_markdown = any("LOSS_MAKING" in c or "DISCOUNT" in c or "SEGMENT_MARGIN" in c for c in cats.get("pricing", set()))
        inventory_low = any("STOCK" in c for c in cats.get("inventory", set()))
        if pricing_wants_markdown and inventory_low:
            low = next((m.get("value") for m in by.get("inventory", DomainSnapshot(agent="i", display_name="i")).metrics if m.get("label") == "Low Stock"), None)
            conflicts.append(Conflict(
                between=["pricing", "inventory"],
                description=(
                    "Pricing wants to discount/clear slow or low-margin stock; Inventory is short "
                    f"({low if low is not None else 'n/a'} low-stock item(s)) and a markdown would accelerate a stockout."
                ),
                resolution=(
                    "Inventory constraint wins for the affected SKUs: no markdown while cover is below "
                    "the reorder point. Apply Pricing's margin floor only to well-stocked SKUs."
                ),
            ))

        marketing_wants_scale = any("DEMAND_CONCENTRATION" in c for c in cats.get("marketing", set()))
        orders_backlog = any("BACKLOG" in c or "FULFILLMENT" in c for c in cats.get("orders", set()))
        if marketing_wants_scale and orders_backlog:
            backlog = next((m.get("value") for m in by.get("orders", DomainSnapshot(agent="o", display_name="o")).metrics if m.get("label") == "Pending Backlog"), None)
            conflicts.append(Conflict(
                between=["marketing", "orders"],
                description=(
                    "Marketing wants to scale acquisition on the hero category; Orders has a fulfilment "
                    f"backlog (pending: {backlog if backlog is not None else 'n/a'}) that more volume would worsen."
                ),
                resolution=(
                    "Delay the acquisition push until the backlog clears below its empirical fence; "
                    "in the interim run retention campaigns (no new fulfilment load)."
                ),
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
