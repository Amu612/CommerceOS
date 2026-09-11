"""Pricing & Margin LangChain tools — real SQL + statistics, empirical severities."""
from __future__ import annotations

from langchain_core.tools import tool

from app.agents._shared import fmt_evidence, money, pct, severity_from_fraction
from app.agents.pricing.data_layer import PricingData
from app.database.session import SessionLocal


def _db():
    return SessionLocal()


@tool
def margin_overview() -> dict:
    """Blended margin, revenue, profit, median/P25 order margin, and the loss-making-order share."""
    db = _db()
    try:
        return PricingData.margin_overview(db)
    finally:
        db.close()


@tool
def margin_by_segment() -> dict:
    """Margin %, revenue, and profit per market segment. Use for 'which segment has the worst/best margin'."""
    db = _db()
    try:
        return PricingData.margin_by_segment(db)
    finally:
        db.close()


@tool
def discount_leakage() -> dict:
    """Per-category average discount rate vs profit ratio and total discount given. Use for 'where is discount leaking'."""
    db = _db()
    try:
        return PricingData.discount_leakage(db)
    finally:
        db.close()


@tool
def category_price_benchmarks() -> dict:
    """Per-category average price, average freight, and freight as a share of price. Use for 'freight drag' / price positioning."""
    db = _db()
    try:
        return PricingData.olist_category_prices(db)
    finally:
        db.close()


@tool
def detect_loss_making_orders() -> dict:
    """Flags a material share of orders shipping below zero margin; severity from the empirical share."""
    db = _db()
    try:
        m = PricingData.margin_overview(db)
        if m.get("status") != "OK" or m["loss_making_rate_pct"] <= 2:
            return {"finding": None}
        frac = m["loss_making_orders"] / max(1, m["sample_count"])
        return {"finding": {
            "category": "LOSS_MAKING_ORDERS",
            "severity": severity_from_fraction(frac),
            "title": "A material share of orders ship below zero margin",
            "what_happened": f"{m['loss_making_orders']:,} of {m['sample_count']:,} orders ({m['loss_making_rate_pct']}%) had negative profit; the 25th-percentile order margin is {m['p25_margin_pct']}%.",
            "why_it_matters": "Loss-making orders convert marketing spend into direct cash loss and often cluster around over-discounted SKUs or high-freight lanes.",
            "recommended_action": "Identify the SKUs/segments in the loss tail; set a minimum-margin floor in the pricing rules; cap stackable discounts.",
            "evidence": fmt_evidence({k: m[k] for k in ("loss_making_orders", "loss_making_rate_pct", "p25_margin_pct", "lower_fence_pct")}),
            "confidence": 0.85, "data_status": "CALCULATED", "sample_count": m["sample_count"],
        }}
    finally:
        db.close()


@tool
def detect_weak_segment() -> dict:
    """Flags the market segment whose margin most drags the blended figure (min 30 orders)."""
    db = _db()
    try:
        m = PricingData.margin_overview(db)
        seg = PricingData.margin_by_segment(db)["segments"]
        if m.get("status") != "OK":
            return {"finding": None}
        weak = min((s for s in seg if s["orders"] >= 30), key=lambda s: s["margin_pct"], default=None)
        if not weak or weak["margin_pct"] >= m["blended_margin_pct"]:
            return {"finding": None}
        return {"finding": {
            "category": "SEGMENT_MARGIN",
            "severity": "HIGH" if weak["margin_pct"] < 0 else "MEDIUM",
            "title": f"Segment '{weak['segment']}' drags blended margin",
            "what_happened": f"'{weak['segment']}' runs {weak['margin_pct']}% margin ({money(weak['profit'])} on {money(weak['revenue'])}) vs blended {m['blended_margin_pct']}%, across {weak['orders']:,} orders.",
            "why_it_matters": "A structurally low-margin segment needs a different price/promo posture than the rest of the book.",
            "recommended_action": f"Raise floor prices or reduce promotional depth in '{weak['segment']}'; re-check freight subsidy for that market.",
            "evidence": fmt_evidence(weak), "confidence": 0.8, "data_status": "CALCULATED", "sample_count": weak["orders"],
        }}
    finally:
        db.close()


@tool
def detect_discount_leakage() -> dict:
    """Flags the category where average discount most outpaces the profit ratio it earns."""
    db = _db()
    try:
        leak = PricingData.discount_leakage(db)
        if not leak["available"]:
            return {"finding": None}
        leaky = max((c for c in leak["categories"] if c["lines"] >= 20 and c["avg_discount_rate_pct"] > 0),
                    key=lambda c: c["avg_discount_rate_pct"] - c["avg_profit_ratio_pct"], default=None)
        if not leaky or leaky["avg_discount_rate_pct"] <= leaky["avg_profit_ratio_pct"]:
            return {"finding": None}
        return {"finding": {
            "category": "DISCOUNT_LEAKAGE",
            "severity": "MEDIUM",
            "title": f"Discounting in '{leaky['category']}' outpaces the margin it earns",
            "what_happened": f"'{leaky['category']}' averages {leaky['avg_discount_rate_pct']}% discount but only {leaky['avg_profit_ratio_pct']}% profit ratio; {money(leaky['total_discount_given'])} of discount given.",
            "why_it_matters": "Discount that does not lift volume or margin is a direct giveaway.",
            "recommended_action": f"A/B test a lower discount ceiling in '{leaky['category']}'; move to targeted rather than list-wide promos.",
            "evidence": fmt_evidence(leaky), "confidence": 0.75, "data_status": "CALCULATED", "sample_count": leaky["lines"],
        }}
    finally:
        db.close()


@tool
def detect_freight_drag() -> dict:
    """Flags the category where freight is the largest share of price (min 50 units, >= 25%)."""
    db = _db()
    try:
        cats = PricingData.olist_category_prices(db)["categories"]
        hf = max((c for c in cats if c["units"] >= 50), key=lambda c: c["freight_pct_of_price"], default=None)
        if not hf or hf["freight_pct_of_price"] < 25:
            return {"finding": None}
        return {"finding": {
            "category": "FREIGHT_DRAG",
            "severity": "MEDIUM",
            "title": f"Freight is {hf['freight_pct_of_price']}% of price in '{hf['category']}'",
            "what_happened": f"'{hf['category']}' averages {money(hf['avg_price'])} price with {money(hf['avg_freight'])} freight ({hf['freight_pct_of_price']}%), {hf['units']:,} units.",
            "why_it_matters": "High freight-to-price ratio quietly erases margin and inflates cart abandonment.",
            "recommended_action": f"Bundle or raise the base price in '{hf['category']}' to absorb freight; renegotiate carrier rate for its typical weight band.",
            "evidence": fmt_evidence(hf), "confidence": 0.8, "data_status": "CALCULATED", "sample_count": hf["units"],
        }}
    finally:
        db.close()


@tool
def order_margin_lookup(order_id: str) -> dict:
    """Look up revenue, profit, and margin % for ONE specific order id. Use this whenever the question names a specific order."""
    db = _db()
    try:
        return PricingData.order_margin_lookup(db, order_id)
    finally:
        db.close()


@tool
def competitor_price_benchmark(category: str) -> dict:
    """Compares our average price in a category against competitor prices scraped via Apify. Use for 'how do we compare to competitors' / 'are we priced right'."""
    from app.core.settings import settings
    from app.models.competitor import CompetitorPrice

    if not settings.apify_configured:
        return {"status": "NOT_CONFIGURED", "reason": "Apify competitor price feed isn't set up yet."}

    db = _db()
    try:
        rows = (
            db.query(CompetitorPrice)
            .filter(CompetitorPrice.category.ilike(f"%{category}%"))
            .order_by(CompetitorPrice.scraped_at.desc())
            .limit(20)
            .all()
        )
        if not rows:
            return {"status": "NOT_ESTIMABLE", "reason": f"No competitor price data scraped yet for '{category}'."}

        ours = PricingData.olist_category_prices(db)["categories"]
        our_match = next((c for c in ours if category.lower() in c["category"].lower()), None)

        comp_prices = [r.price for r in rows]
        avg_competitor = round(sum(comp_prices) / len(comp_prices), 2)
        return {
            "status": "OK",
            "category": category,
            "our_avg_price": our_match["avg_price"] if our_match else None,
            "competitor_avg_price": avg_competitor,
            "competitor_min_price": round(min(comp_prices), 2),
            "competitor_max_price": round(max(comp_prices), 2),
            "sample_count": len(rows),
            "competitors": sorted({r.competitor_name for r in rows}),
            "gap_pct": pct(our_match["avg_price"] - avg_competitor, avg_competitor) if our_match else None,
        }
    finally:
        db.close()


METRIC_TOOLS = [margin_overview, margin_by_segment, discount_leakage, category_price_benchmarks]
DETECTOR_TOOLS = [detect_loss_making_orders, detect_weak_segment, detect_discount_leakage, detect_freight_drag]
LOOKUP_TOOLS = [order_margin_lookup, competitor_price_benchmark]
