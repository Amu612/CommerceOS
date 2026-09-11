"""
Logistics LangChain tools. Each tool is the real, data-driven computation
(SQL + `app.intelligence` statistics); severities come from the observed
distribution, never a hardcoded constant.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.agents._shared import fmt_evidence, severity_from_fraction
from app.agents.logistics.data_layer import LogisticsData
from app.database.session import SessionLocal


def _db():
    return SessionLocal()


# ── metric tools ───────────────────────────────────────────────
@tool
def logistics_overview() -> dict:
    """Shipment volume and the share of shipments carrying a late-delivery-risk flag, plus the Olist delivery-SLA summary."""
    db = _db()
    try:
        risk = LogisticsData.late_risk_overview(db)
        sla = LogisticsData.olist_delivery_sla(db)
        return {"late_risk": risk, "olist_sla": sla, "sample_count": risk.get("total_shipments", 0)}
    finally:
        db.close()


@tool
def carrier_scorecard() -> dict:
    """Per-carrier (shipping mode) transit days vs scheduled, average delay, and late-risk rate. Use to answer 'which carrier is slowest/best'."""
    db = _db()
    try:
        return LogisticsData.carrier_performance(db)
    finally:
        db.close()


@tool
def lane_scorecard() -> dict:
    """Per-lane (order region) transit time, SLA gap in days, and late-risk rate. Use to answer 'which lane/region/route is worst'."""
    db = _db()
    try:
        return LogisticsData.lane_performance(db)
    finally:
        db.close()


@tool
def transit_time_distribution() -> dict:
    """Empirical distribution of real transit days: median, P90, max, Tukey outlier fence, and outlier count."""
    db = _db()
    try:
        return LogisticsData.transit_distribution(db)
    finally:
        db.close()


@tool
def olist_delivery_sla() -> dict:
    """Olist delivered-order SLA: on-time rate, late count, median/P90 delivery-vs-promise margin in days, and the derived SLA health."""
    db = _db()
    try:
        return LogisticsData.olist_delivery_sla(db)
    finally:
        db.close()


# ── detector tools (emit a Finding from empirical analysis) ─────
@tool
def detect_late_delivery_risk() -> dict:
    """Flags an elevated share of shipments with a late-delivery-risk flag; severity from the empirical share."""
    db = _db()
    try:
        r = LogisticsData.late_risk_overview(db)
        total, at_risk = r["total_shipments"], r["at_risk"]
        if total == 0 or at_risk == 0:
            return {"finding": None}
        frac = at_risk / total
        return {"finding": {
            "category": "LATE_DELIVERY_RISK",
            "severity": severity_from_fraction(frac),
            "title": "Elevated late-delivery risk across the shipment base",
            "what_happened": f"{at_risk:,} of {total:,} shipments ({r['at_risk_rate_pct']}%) carry a late-delivery-risk flag.",
            "why_it_matters": "Late deliveries drive support contacts, refund requests, and repeat-purchase churn.",
            "recommended_action": "Prioritise the worst lanes/carriers; pre-emptively notify customers on flagged shipments and pad promised delivery dates for those lanes.",
            "evidence": f"at_risk={at_risk}, total={total}, rate={r['at_risk_rate_pct']}%",
            "confidence": 0.9, "data_status": "CALCULATED", "sample_count": total,
        }}
    finally:
        db.close()


@tool
def detect_slow_carrier() -> dict:
    """Flags the carrier with the largest average delay vs its own schedule (min 20 shipments)."""
    db = _db()
    try:
        cs = LogisticsData.carrier_performance(db)["carriers"]
        worst = max((c for c in cs if c["shipments"] >= 20), key=lambda c: c["avg_delay_days"], default=None)
        if not worst or worst["avg_delay_days"] <= 0:
            return {"finding": None}
        return {"finding": {
            "category": "CARRIER_PERFORMANCE",
            "severity": "HIGH" if worst["avg_delay_days"] >= 1.5 else "MEDIUM",
            "title": f"Carrier '{worst['carrier']}' is running behind schedule",
            "what_happened": f"'{worst['carrier']}' averages {worst['avg_transit_days']}d transit vs {worst['avg_scheduled_days']}d scheduled (+{worst['avg_delay_days']}d) over {worst['shipments']:,} shipments, {worst['late_risk_rate_pct']}% flagged.",
            "why_it_matters": "A structurally slow carrier degrades SLA on every order routed through it.",
            "recommended_action": f"Re-weight routing away from '{worst['carrier']}' for time-sensitive orders; open a carrier performance review; adjust its promised-date model by +{worst['avg_delay_days']}d.",
            "evidence": fmt_evidence(worst), "confidence": 0.85, "data_status": "CALCULATED", "sample_count": worst["shipments"],
        }}
    finally:
        db.close()


@tool
def detect_lane_bottleneck() -> dict:
    """Flags the lane/region with the widest SLA gap (min 20 shipments)."""
    db = _db()
    try:
        ls = LogisticsData.lane_performance(db)["lanes"]
        worst = max((l for l in ls if l["shipments"] >= 20), key=lambda l: l["sla_gap_days"], default=None)
        if not worst or worst["sla_gap_days"] <= 0:
            return {"finding": None}
        return {"finding": {
            "category": "LANE_BOTTLENECK",
            "severity": "HIGH" if worst["sla_gap_days"] >= 2 else "MEDIUM",
            "title": f"Lane '{worst['lane']}' has the widest SLA gap",
            "what_happened": f"'{worst['lane']}' delivers in {worst['avg_transit_days']}d, {worst['sla_gap_days']}d over its scheduled window, {worst['late_risk_rate_pct']}% flagged ({worst['shipments']:,} shipments).",
            "why_it_matters": "Concentrated lane delay points to a hub or customs bottleneck that compounds across every carrier on that lane.",
            "recommended_action": f"Audit the '{worst['lane']}' distribution hub and customs clearance; consider an alternate hub or expedited tier for that region.",
            "evidence": fmt_evidence(worst), "confidence": 0.85, "data_status": "CALCULATED", "sample_count": worst["shipments"],
        }}
    finally:
        db.close()


@tool
def detect_sla_degradation() -> dict:
    """Flags Olist delivery SLA when the delivery-vs-promise margin distribution is AT_RISK or DEGRADED."""
    db = _db()
    try:
        s = LogisticsData.olist_delivery_sla(db)
        if s.get("status") != "OK" or s.get("sla_health") not in ("AT_RISK", "DEGRADED"):
            return {"finding": None}
        return {"finding": {
            "category": "DELIVERY_SLA",
            "severity": "HIGH" if s["sla_health"] == "DEGRADED" else "MEDIUM",
            "title": f"Delivery SLA is {s['sla_health']}",
            "what_happened": f"{s['late_deliveries']:,} of {s['sample_count']:,} delivered orders arrived after the promised date (median margin {s['median_margin_days']}d, P90 {s['p90_margin_days']}d).",
            "why_it_matters": "Systematic overshoot of promised dates erodes trust and inflates 'where is my order' contacts.",
            "recommended_action": "Recalibrate the estimated-delivery-date model per region using the observed margin distribution; add a buffer equal to the P75 margin.",
            "evidence": fmt_evidence({k: s[k] for k in ("on_time_rate_pct", "median_margin_days", "p90_margin_days")}),
            "confidence": 0.85, "data_status": "CALCULATED", "sample_count": s["sample_count"],
        }}
    finally:
        db.close()


@tool
def shipment_lookup(order_id: str) -> dict:
    """Look up shipping mode/carrier, scheduled vs actual transit days, and late-delivery-risk for ONE specific order id. Use this whenever the question names a specific order/shipment."""
    db = _db()
    try:
        return LogisticsData.shipment_lookup(db, order_id)
    finally:
        db.close()


METRIC_TOOLS = [logistics_overview, carrier_scorecard, lane_scorecard, transit_time_distribution, olist_delivery_sla]
DETECTOR_TOOLS = [detect_late_delivery_risk, detect_slow_carrier, detect_lane_bottleneck, detect_sla_degradation]
LOOKUP_TOOLS = [shipment_lookup]
