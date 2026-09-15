"""Pricing & Margin LangChain tools — real SQL + statistics, empirical severities."""

from __future__ import annotations

from langchain_core.tools import tool
from sqlalchemy import func

from app.agents._shared import fmt_evidence, money, pct, sample_confidence, severity_from_fraction
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
        return {
            "finding": {
                "category": "LOSS_MAKING_ORDERS",
                "severity": severity_from_fraction(frac),
                "title": "Some orders are losing money",
                "what_happened": f"{m['loss_making_orders']:,} out of {m['sample_count']:,} orders ({m['loss_making_rate_pct']}%) lost money instead of making a profit.",
                "why_it_matters": "Selling items at a loss burns through cash and wastes advertising money.",
                "recommended_action": "Find items that lose money and raise their price. Do not allow buyers to combine too many discounts.",
                "evidence": fmt_evidence(
                    {
                        k: m[k]
                        for k in (
                            "loss_making_orders",
                            "loss_making_rate_pct",
                            "p25_margin_pct",
                            "lower_fence_pct",
                        )
                    }
                ),
                "confidence": sample_confidence(m["sample_count"]),
                "data_status": "CALCULATED",
                "sample_count": m["sample_count"],
            }
        }
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
        return {
            "finding": {
                "category": "SEGMENT_MARGIN",
                "severity": "HIGH" if weak["margin_pct"] < 0 else "MEDIUM",
                "title": f"Sales to '{weak['segment']}' make very little profit",
                "what_happened": f"Sales to '{weak['segment']}' only make {weak['margin_pct']}% profit ({money(weak['profit'])} profit on {money(weak['revenue'])} in sales), which is much lower than normal.",
                "why_it_matters": "If a big group of buyers makes low profit, overall business earnings go down.",
                "recommended_action": f"Lower discounts and charge fair shipping costs for buyers in '{weak['segment']}'.",
                "evidence": fmt_evidence(weak),
                "confidence": sample_confidence(weak["orders"]),
                "data_status": "CALCULATED",
                "sample_count": weak["orders"],
            }
        }
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
        leaky = max(
            (c for c in leak["categories"] if c["lines"] >= 20 and c["avg_discount_rate_pct"] > 0),
            key=lambda c: c["avg_discount_rate_pct"] - c["avg_profit_ratio_pct"],
            default=None,
        )
        if not leaky or leaky["avg_discount_rate_pct"] <= leaky["avg_profit_ratio_pct"]:
            return {"finding": None}
        return {
            "finding": {
                "category": "DISCOUNT_LEAKAGE",
                "severity": "MEDIUM",
                "title": f"Discounts are too high in '{leaky['category']}'",
                "what_happened": f"Items in '{leaky['category']}' have an average discount of {leaky['avg_discount_rate_pct']}%, leaving almost no profit ({money(leaky['total_discount_given'])} given away in discounts).",
                "why_it_matters": "Giving away big discounts without gaining extra sales is simply losing money.",
                "recommended_action": f"Cut back on discounts for '{leaky['category']}' and only offer coupons to buyers who really need them to purchase.",
                "evidence": fmt_evidence(leaky),
                "confidence": sample_confidence(leaky["lines"]),
                "data_status": "CALCULATED",
                "sample_count": leaky["lines"],
            }
        }
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
        return {
            "finding": {
                "category": "FREIGHT_DRAG",
                "severity": "MEDIUM",
                "title": f"Shipping costs eat up sales for '{hf['category']}'",
                "what_happened": f"In '{hf['category']}', shipping costs ({money(hf['avg_freight'])}) make up {hf['freight_pct_of_price']}% of the item price ({money(hf['avg_price'])}).",
                "why_it_matters": "When shipping costs are too high, buyers leave their carts without paying, or shipping wipes out our earnings.",
                "recommended_action": "Sell items in pairs/bundles or adjust the item price to cover delivery costs.",
                "evidence": fmt_evidence(hf),
                "confidence": sample_confidence(hf["units"]),
                "data_status": "CALCULATED",
                "sample_count": hf["units"],
            }
        }
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
            return {
                "status": "NOT_ESTIMABLE",
                "reason": f"No competitor price data scraped yet for '{category}'.",
            }

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
DETECTOR_TOOLS = [
    detect_loss_making_orders,
    detect_weak_segment,
    detect_discount_leakage,
    detect_freight_drag,
]
LOOKUP_TOOLS = [order_margin_lookup, competitor_price_benchmark]


# ── Doc-grade pricing analytics ─────────────────────────────────────────────
@tool
def pricing_analytics(metric: str) -> str:
    """Product pricing / freight analytics across categories and sellers.

    metric must be one of:
    - "category_price_stats"  : avg, median, min, max product price per category
    - "price_skew"            : categories with the largest avg-vs-median price gap
    - "freight_burden"        : freight as % of price per category; 5 highest
    - "popular_vs_rest"       : avg price of the top-10% most frequently sold products vs the rest
    - "price_freight_corr"    : relationship (Pearson r) between product price and freight value
    - "freight_heavy"         : % of products whose freight exceeds 50% of their price
    - "category_value_rank"   : total merchandise value + avg selling price ranking by category
    - "seller_price_variation": categories with the largest seller-to-seller average price variation
    - "increase_scenario"     : +5% price on the top-5 revenue categories -> theoretical additional revenue
    """
    import statistics as st
    from collections import defaultdict

    from app.agents._shared import simulated_clock
    from app.models.olist import CategoryTranslation, Order, OrderItem, Product, Seller

    db = _db()
    try:
        clock = simulated_clock(db)
        rows = (
            db.query(
                Product.product_category_name,
                OrderItem.product_id,
                Seller.seller_id,
                OrderItem.price,
                OrderItem.freight_value,
                func.count(OrderItem.order_item_id),
            )
            .join(Order, Order.order_id == OrderItem.order_id)
            .join(Product, Product.product_id == OrderItem.product_id)
            .join(Seller, Seller.seller_id == OrderItem.seller_id)
            .filter(Order.order_purchase_timestamp <= clock)
            .group_by(Product.product_category_name, OrderItem.product_id, Seller.seller_id)
            .all()
        )
        if not rows:
            return "No pricing data observed yet."
        cat_en = {
            r[0]: r[1]
            for r in db.query(
                CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english
            ).all()
        }

        def disp(c):
            return cat_en.get(c, (c or "unknown")).replace("_", " ").title()

        cat_prices: dict = defaultdict(list)
        cat_freight: dict = defaultdict(list)
        cat_revenue: dict = defaultdict(float)
        prod_freq: dict = defaultdict(int)
        prod_price: dict = defaultdict(list)
        seller_cat_price: dict = defaultdict(dict)
        total_revenue = 0.0

        for cat, pid, sid, price, freight, qty in rows:
            p, f = float(price or 0), float(freight or 0)
            cat_prices[cat].append(p)
            if p > 0:
                cat_freight[cat].append(f / p)
            cat_revenue[cat] += p * qty
            total_revenue += p * qty
            prod_freq[pid] += int(qty)
            prod_price[pid].append(p)
            seller_cat_price[cat][sid] = p

        if metric == "category_price_stats":
            parts = "; ".join(
                f"{disp(c)}: avg R${sum(v) / len(v):,.0f}, med R${st.median(v):,.0f}, min R${min(v):,.0f}, max R${max(v):,.0f}"
                for c, v in sorted(cat_prices.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:8]
            )
            return f"Price stats per category (top 8 by avg price): {parts}"
        if metric == "price_skew":
            gaps = sorted(
                ((c, sum(v) / len(v) - st.median(v)) for c, v in cat_prices.items() if len(v) >= 5),
                key=lambda x: -x[1],
            )
            parts = ", ".join(f"{disp(c)} R${g:,.0f}" for c, g in gaps[:6])
            return f"Largest avg-vs-median price gaps (right-skewed pricing): {parts}"
        if metric == "freight_burden":
            burdens = sorted(
                ((c, sum(v) / len(v) * 100) for c, v in cat_freight.items() if v),
                key=lambda x: -x[1],
            )
            parts = ", ".join(f"{disp(c)} {b:.1f}%" for c, b in burdens[:5])
            return f"Five categories with the highest average freight-to-price ratio: {parts}"
        if metric == "popular_vs_rest":
            n_top = max(1, len(prod_freq) // 10)
            top_ids = set(sorted(prod_freq, key=lambda k: prod_freq[k], reverse=True)[:n_top])
            top_prices = [p for pid in top_ids for p in prod_price[pid]]
            rest_prices = [p for pid, ps in prod_price.items() if pid not in top_ids for p in ps]
            return (
                f"Top 10% most frequently sold products: avg price R${sum(top_prices) / max(1, len(top_prices)):,.2f}. "
                f"Remaining products: R${sum(rest_prices) / max(1, len(rest_prices)):,.2f}."
            )
        if metric == "price_freight_corr":
            line_rows = (
                db.query(OrderItem.price, OrderItem.freight_value)
                .join(Order, Order.order_id == OrderItem.order_id)
                .filter(Order.order_purchase_timestamp <= clock)
                .limit(20000)
                .all()
            )
            xs = [float(p or 0) for p, _ in line_rows]
            ys = [float(f or 0) for _, f in line_rows]
            if len(xs) > 2 and st.pstdev(xs) > 0 and st.pstdev(ys) > 0:
                mx, my = st.mean(xs), st.mean(ys)
                cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False)) / len(xs)
                r = cov / (st.pstdev(xs) * st.pstdev(ys))
                verdict = (
                    "freight generally rises with price"
                    if r > 0.3
                    else ("weak relationship" if r > 0 else "no positive relationship")
                )
                return f"Pearson r between product price and freight = {r:.2f} over {len(xs):,} line items — {verdict}."
            return "Not enough data for a correlation."
        if metric == "freight_heavy":
            rows2 = (
                db.query(OrderItem.price, OrderItem.freight_value)
                .join(Order, Order.order_id == OrderItem.order_id)
                .filter(Order.order_purchase_timestamp <= clock)
                .limit(20000)
                .all()
            )
            heavy = sum(1 for p, f in rows2 if float(p or 0) > 0 and float(f or 0) > 0.5 * float(p))
            return f"{heavy:,} of {len(rows2):,} line items ({heavy / max(1, len(rows2)) * 100:.1f}%) have freight exceeding 50% of the product price."
        if metric == "category_value_rank":
            parts = ", ".join(
                f"{disp(c)} R${v:,.0f} (avg R${sum(cat_prices[c]) / len(cat_prices[c]):,.0f})"
                for c, v in sorted(cat_revenue.items(), key=lambda kv: -kv[1])[:8]
            )
            return f"Total merchandise value by category (top 8): {parts}"
        if metric == "seller_price_variation":
            vars_ = []
            for cat, sellers in seller_cat_price.items():
                if len(sellers) >= 5:
                    prices = list(sellers.values())
                    vars_.append((cat, max(prices) - min(prices), len(sellers)))
            vars_.sort(key=lambda x: -x[1])
            parts = ", ".join(f"{disp(c)} R${g:,.0f} spread across {n} sellers" for c, g, n in vars_[:6])
            return f"Largest seller-to-seller price variation by category: {parts}"
        if metric == "increase_scenario":
            top5 = sorted(cat_revenue.items(), key=lambda kv: -kv[1])[:5]
            extra = sum(v for _, v in top5) * 0.05
            parts = ", ".join(f"{disp(c)} R${v:,.0f}" for c, v in top5)
            return (
                f"Top 5 revenue categories: {parts}. A +5% price increase at constant volume would add "
                f"~R${extra:,.2f} in theoretical revenue."
            )
        return (
            "Unknown metric. Use one of: category_price_stats, price_skew, freight_burden, popular_vs_rest, "
            "price_freight_corr, freight_heavy, category_value_rank, seller_price_variation, increase_scenario"
        )
    except Exception as exc:
        return f"❌ Analytics failed: {exc}"
    finally:
        db.close()


LOOKUP_TOOLS = [order_margin_lookup, competitor_price_benchmark, pricing_analytics]
