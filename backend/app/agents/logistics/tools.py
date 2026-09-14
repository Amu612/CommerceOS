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
            "title": "Many packages are at risk of arriving late",
            "what_happened": f"{at_risk:,} out of {total:,} packages ({r['at_risk_rate_pct']}%) are likely to be late.",
            "why_it_matters": "When packages arrive late, customers get upset, ask for money back, and stop buying from us.",
            "recommended_action": "Fix the slowest delivery routes first. Tell buyers about delays early and add extra days to delivery estimates.",
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
            "title": f"Delivery company '{worst['carrier']}' is too slow",
            "what_happened": f"Delivery company '{worst['carrier']}' takes {worst['avg_transit_days']} days instead of the planned {worst['avg_scheduled_days']} days (it is {worst['avg_delay_days']} days late on average across {worst['shipments']:,} packages).",
            "why_it_matters": "Using a slow delivery company makes packages late and hurts our customer reviews.",
            "recommended_action": f"Send rush orders using faster delivery companies instead. Add {worst['avg_delay_days']} days to expected delivery dates for '{worst['carrier']}'.",
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
            "title": f"Shipping to '{worst['lane']}' takes too long",
            "what_happened": f"Deliveries to '{worst['lane']}' take {worst['avg_transit_days']} days, which is {worst['sla_gap_days']} days slower than promised ({worst['late_risk_rate_pct']}% are late).",
            "why_it_matters": "This entire region has shipping slowdowns, meaning almost all packages sent here arrive late.",
            "recommended_action": f"Check the sorting center for '{worst['lane']}' to see what is stuck, or try shipping from a closer warehouse.",
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
            "title": "Too many packages are delivered past their promised date",
            "what_happened": f"{s['late_deliveries']:,} out of {s['sample_count']:,} orders arrived later than the promised date.",
            "why_it_matters": "Missing delivery promises makes buyers lose trust and call support asking where their orders are.",
            "recommended_action": "Show buyers safer delivery dates with a few extra days added so packages arrive on time.",
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
