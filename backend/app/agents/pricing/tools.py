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
            "title": "Some orders are losing money",
            "what_happened": f"{m['loss_making_orders']:,} out of {m['sample_count']:,} orders ({m['loss_making_rate_pct']}%) lost money instead of making a profit.",
            "why_it_matters": "Selling items at a loss burns through cash and wastes advertising money.",
            "recommended_action": "Find items that lose money and raise their price. Do not allow buyers to combine too many discounts.",
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
            "title": f"Sales to '{weak['segment']}' make very little profit",
            "what_happened": f"Sales to '{weak['segment']}' only make {weak['margin_pct']}% profit ({money(weak['profit'])} profit on {money(weak['revenue'])} in sales), which is much lower than normal.",
            "why_it_matters": "If a big group of buyers makes low profit, overall business earnings go down.",
            "recommended_action": f"Lower discounts and charge fair shipping costs for buyers in '{weak['segment']}'.",
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
            "title": f"Discounts are too high in '{leaky['category']}'",
            "what_happened": f"Items in '{leaky['category']}' have an average discount of {leaky['avg_discount_rate_pct']}%, leaving almost no profit ({money(leaky['total_discount_given'])} given away in discounts).",
            "why_it_matters": "Giving away big discounts without gaining extra sales is simply losing money.",
            "recommended_action": f"Cut back on discounts for '{leaky['category']}' and only offer coupons to buyers who really need them to purchase.",
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
            "title": f"Shipping costs eat up sales for '{hf['category']}'",
            "what_happened": f"In '{hf['category']}', shipping costs ({money(hf['avg_freight'])}) make up {hf['freight_pct_of_price']}% of the item price ({money(hf['avg_price'])}).",
            "why_it_matters": "When shipping costs are too high, buyers leave their carts without paying, or shipping wipes out our earnings.",
            "recommended_action": f"Sell items in pairs/bundles or adjust the item price to cover delivery costs.",
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
