"""
Logistics LangChain tools. Each tool is the real, data-driven computation
(SQL + `app.intelligence` statistics); severities come from the observed
distribution, never a hardcoded constant.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agents._shared import fmt_evidence, sample_confidence, severity_from_fraction
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
        return {
            "finding": {
                "category": "LATE_DELIVERY_RISK",
                "severity": severity_from_fraction(frac),
                "title": "Many packages are at risk of arriving late",
                "what_happened": f"{at_risk:,} out of {total:,} packages ({r['at_risk_rate_pct']}%) are likely to be late.",
                "why_it_matters": "When packages arrive late, customers get upset, ask for money back, and stop buying from us.",
                "recommended_action": "Fix the slowest delivery routes first. Tell buyers about delays early and add extra days to delivery estimates.",
                "evidence": f"at_risk={at_risk}, total={total}, rate={r['at_risk_rate_pct']}%",
                "confidence": sample_confidence(total),
                "data_status": "CALCULATED",
                "sample_count": total,
            }
        }
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
        return {
            "finding": {
                "category": "CARRIER_PERFORMANCE",
                "severity": "HIGH" if worst["avg_delay_days"] >= 1.5 else "MEDIUM",
                "title": f"Delivery company '{worst['carrier']}' is too slow",
                "what_happened": f"Delivery company '{worst['carrier']}' takes {worst['avg_transit_days']} days instead of the planned {worst['avg_scheduled_days']} days (it is {worst['avg_delay_days']} days late on average across {worst['shipments']:,} packages).",
                "why_it_matters": "Using a slow delivery company makes packages late and hurts our customer reviews.",
                "recommended_action": f"Send rush orders using faster delivery companies instead. Add {worst['avg_delay_days']} days to expected delivery dates for '{worst['carrier']}'.",
                "evidence": fmt_evidence(worst),
                "confidence": sample_confidence(worst["shipments"]),
                "data_status": "CALCULATED",
                "sample_count": worst["shipments"],
            }
        }
    finally:
        db.close()


@tool
def detect_lane_bottleneck() -> dict:
    """Flags the lane/region with the widest SLA gap (min 20 shipments)."""
    db = _db()
    try:
        ls = LogisticsData.lane_performance(db)["lanes"]
        worst = max(
            (lane for lane in ls if lane["shipments"] >= 20),
            key=lambda lane: lane["sla_gap_days"],
            default=None,
        )
        if not worst or worst["sla_gap_days"] <= 0:
            return {"finding": None}
        return {
            "finding": {
                "category": "LANE_BOTTLENECK",
                "severity": "HIGH" if worst["sla_gap_days"] >= 2 else "MEDIUM",
                "title": f"Shipping to '{worst['lane']}' takes too long",
                "what_happened": f"Deliveries to '{worst['lane']}' take {worst['avg_transit_days']} days, which is {worst['sla_gap_days']} days slower than promised ({worst['late_risk_rate_pct']}% are late).",
                "why_it_matters": "This entire region has shipping slowdowns, meaning almost all packages sent here arrive late.",
                "recommended_action": f"Check the sorting center for '{worst['lane']}' to see what is stuck, or try shipping from a closer warehouse.",
                "evidence": fmt_evidence(worst),
                "confidence": sample_confidence(worst["shipments"]),
                "data_status": "CALCULATED",
                "sample_count": worst["shipments"],
            }
        }
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
        return {
            "finding": {
                "category": "DELIVERY_SLA",
                "severity": "HIGH" if s["sla_health"] == "DEGRADED" else "MEDIUM",
                "title": "Too many packages are delivered past their promised date",
                "what_happened": f"{s['late_deliveries']:,} out of {s['sample_count']:,} orders arrived later than the promised date.",
                "why_it_matters": "Missing delivery promises makes buyers lose trust and call support asking where their orders are.",
                "recommended_action": "Show buyers safer delivery dates with a few extra days added so packages arrive on time.",
                "evidence": fmt_evidence(
                    {k: s[k] for k in ("on_time_rate_pct", "median_margin_days", "p90_margin_days")}
                ),
                "confidence": sample_confidence(s["sample_count"]),
                "data_status": "CALCULATED",
                "sample_count": s["sample_count"],
            }
        }
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


METRIC_TOOLS = [
    logistics_overview,
    carrier_scorecard,
    lane_scorecard,
    transit_time_distribution,
    olist_delivery_sla,
]
DETECTOR_TOOLS = [
    detect_late_delivery_risk,
    detect_slow_carrier,
    detect_lane_bottleneck,
    detect_sla_degradation,
]
LOOKUP_TOOLS = [shipment_lookup]


# ── Doc-grade logistics analytics ───────────────────────────────────────────
@tool
def logistics_analytics(metric: str) -> str:
    """Delivery-duration / delay / seller-performance analytics from real orders.

    metric must be one of:
    - "delivery_by_state"   : average delivery days per customer state; 5 slowest
    - "late_pct"            : % of delivered orders arriving after the estimated date
    - "avg_days_late"       : average days late among delayed orders
    - "seller_late_pct"     : sellers (>=20 delivered orders) by late-delivery percentage
    - "monthly_delay_gap"   : estimated-vs-actual delivery gap by month; months with the largest delays
    - "category_delay"      : average delivery delay in days by product category
    - "state_spread"        : fastest vs slowest state delivery times and the % difference
    - "punctuality_split"   : % of orders delivered early / on time / late
    - "volume_vs_late"      : sellers with both high order volume and poor delivery performance
    - "delivery_by_year"    : average purchase-to-delivery days per year; improving or deteriorating
    - "seller_risk_rank"    : 10 sellers creating the greatest logistics risk (volume x late% x avg delay), calculation shown
    """
    import statistics as st
    from collections import defaultdict

    from app.agents._shared import simulated_clock
    from app.models.olist import CategoryTranslation, Customer, Order, OrderItem, Product, Seller

    db = _db()
    try:
        clock = simulated_clock(db)
        rows = (
            db.query(
                Order.order_id,
                Order.order_purchase_timestamp,
                Order.order_delivered_customer_date,
                Order.order_estimated_delivery_date,
                Customer.customer_state,
                Seller.seller_id,
                Product.product_category_name,
            )
            .join(OrderItem, OrderItem.order_id == Order.order_id)
            .join(Customer, Customer.customer_id == Order.customer_id)
            .join(Seller, Seller.seller_id == OrderItem.seller_id)
            .join(Product, Product.product_id == OrderItem.product_id)
            .filter(
                Order.order_purchase_timestamp <= clock,
                Order.order_status == "delivered",
                Order.order_delivered_customer_date.isnot(None),
            )
            .all()
        )
        if not rows:
            return "No delivered orders observed yet."
        cat_en = {
            r[0]: r[1]
            for r in db.query(
                CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english
            ).all()
        }

        def disp(c):
            return cat_en.get(c, (c or "unknown")).replace("_", " ").title()

        state_days: dict = defaultdict(list)
        seller_stats: dict = defaultdict(lambda: {"n": 0, "late": 0, "days_late": []})
        monthly_gap: dict = defaultdict(list)
        cat_delay: dict = defaultdict(list)
        early = on_time = late = 0
        year_days: dict = defaultdict(list)

        for _oid, purch, deliv, est, state, sid, cat in rows:
            if not purch or not deliv:
                continue
            d_days = (deliv - purch).total_seconds() / 86400.0
            state_days[state].append(d_days)
            year_days[purch.year].append(d_days)
            if est:
                diff = (deliv - est).total_seconds() / 86400.0
                if diff < 0:
                    early += 1
                elif diff == 0:
                    on_time += 1
                else:
                    late += 1
                ym = purch.strftime("%Y-%m")
                monthly_gap[ym].append(diff)
                if cat:
                    cat_delay[cat].append(diff)
                s = seller_stats[sid]
                s["n"] += 1
                if diff > 0:
                    s["late"] += 1
                    s["days_late"].append(diff)

        if metric == "delivery_by_state":
            rows2 = sorted(
                ((s, st.mean(v)) for s, v in state_days.items() if len(v) >= 10), key=lambda x: -x[1]
            )
            parts = ", ".join(f"{s}: {d:.1f}d" for s, d in rows2[:5])
            return f"Five slowest states by average delivery time: {parts}"
        if metric == "late_pct":
            total = early + on_time + late or 1
            return f"{late:,} of {total:,} delivered orders ({late / total * 100:.1f}%) arrived after their estimated date."
        if metric == "avg_days_late":
            gaps = [g for v in monthly_gap.values() for g in v if g > 0]
            return f"Average lateness among delayed orders: {st.mean(gaps):.1f} days ({len(gaps):,} delayed deliveries)."
        if metric == "seller_late_pct":
            rows2 = []
            for sid, s in seller_stats.items():
                if s["n"] >= 20:
                    rows2.append((sid, s["late"] / s["n"] * 100, s["n"]))
            rows2.sort(key=lambda x: -x[1])
            parts = ", ".join(f"#{sid[:8]} {p:.0f}% of {n}" for sid, p, n in rows2[:6])
            return "Sellers with the highest late-delivery % (min 20 delivered orders): " + (parts or "none")
        if metric == "monthly_delay_gap":
            rows2 = sorted(
                ((ym, st.mean(v)) for ym, v in monthly_gap.items() if len(v) >= 10), key=lambda x: -x[1]
            )
            parts = ", ".join(f"{ym}: +{g:.1f}d" for ym, g in rows2[:5])
            worst = rows2[0] if rows2 else None
            return f"Months with the largest est-vs-actual delivery gap: {parts}" + (
                f". Worst: {worst[0]}" if worst else ""
            )
        if metric == "category_delay":
            rows2 = sorted(
                ((c, st.mean(v)) for c, v in cat_delay.items() if len(v) >= 10), key=lambda x: -x[1]
            )
            parts = ", ".join(f"{disp(c)}: +{g:.1f}d" for c, g in rows2[:6])
            return "Categories with the highest average delivery delay: " + (parts or "none")
        if metric == "state_spread":
            rows2 = [(s, st.mean(v)) for s, v in state_days.items() if len(v) >= 10]
            if len(rows2) < 2:
                return "Not enough state-level data."
            rows2.sort(key=lambda x: x[1])
            fastest, slowest = rows2[0], rows2[-1]
            pct = (slowest[1] - fastest[1]) / fastest[1] * 100
            return f"Fastest state {fastest[0]}: {fastest[1]:.1f}d vs slowest {slowest[0]}: {slowest[1]:.1f}d — a {pct:.0f}% difference."
        if metric == "punctuality_split":
            total = early + on_time + late or 1
            return f"Delivered orders: {early / total * 100:.1f}% early, {on_time / total * 100:.1f}% on time, {late / total * 100:.1f}% late."
        if metric == "volume_vs_late":
            med_n = st.median([s["n"] for s in seller_stats.values()]) if seller_stats else 0
            out = [
                (sid, s["n"], s["late"] / s["n"] * 100, st.mean(s["days_late"]) if s["days_late"] else 0.0)
                for sid, s in seller_stats.items()
                if s["n"] >= max(20, med_n) and s["late"] / s["n"] > 0.1
            ]
            out.sort(key=lambda x: -x[1])
            parts = ", ".join(
                f"#{sid[:8]} ({n} orders, {p:.0f}% late, +{d:.1f}d avg)" for sid, n, p, d in out[:6]
            )
            return "High-volume sellers with poor delivery: " + (parts or "none")
        if metric == "delivery_by_year":
            rows2 = sorted(((y, st.mean(v)) for y, v in year_days.items()), key=lambda x: x[0])
            parts = ", ".join(f"{y}: {d:.1f}d" for y, d in rows2)
            trend = "improving" if len(rows2) >= 2 and rows2[-1][1] < rows2[0][1] else "deteriorating"
            return f"Average purchase→delivery days per year: {parts} — performance is {trend}."
        if metric == "seller_risk_rank":
            scored = []
            for sid, s in seller_stats.items():
                if s["n"] < 20:
                    continue
                late_p = s["late"] / s["n"] * 100
                avg_late = st.mean(s["days_late"]) if s["days_late"] else 0.0
                risk = (s["n"] / 100.0) * (late_p / 100.0) * max(avg_late, 0.5)
                scored.append((sid, risk, s["n"], late_p, avg_late))
            scored.sort(key=lambda x: -x[1])
            parts = "; ".join(
                f"#{sid[:8]} (risk {r:.2f} = vol {n}/100 x late {p:.0f}% x +{d:.1f}d)"
                for sid, r, n, p, d in scored[:10]
            )
            return "Top 10 logistics-risk sellers (delivered-order volume x late-rate x avg delay): " + (
                parts or "none"
            )
        return (
            "Unknown metric. Use one of: delivery_by_state, late_pct, avg_days_late, seller_late_pct, "
            "monthly_delay_gap, category_delay, state_spread, punctuality_split, volume_vs_late, "
            "delivery_by_year, seller_risk_rank"
        )
    except Exception as exc:
        return f"❌ Analytics failed: {exc}"
    finally:
        db.close()


LOOKUP_TOOLS = [shipment_lookup, logistics_analytics]
