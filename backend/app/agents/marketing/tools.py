"""Marketing LangChain tools — real RFM/demand computation, empirical severities."""

from __future__ import annotations

from langchain_core.tools import tool

from app.agents._shared import fmt_evidence, money, period_bucket, sample_confidence
from app.agents.marketing.data_layer import MarketingData
from app.database.session import SessionLocal

CAMPAIGN_PLAYS = {
    "Champions": "Give them VIP perks, early access, and thank-you rewards to keep them happy.",
    "Loyal": "Recommend similar products and offer points for shopping again.",
    "Promising": "Give a small coupon on their next order before they forget about us.",
    "At Risk": "Send a friendly 'we miss you' email with top-selling items and a special discount.",
    "Hibernating": "Send a simple reminder email; do not waste paid ad money on them.",
    "New": "Welcome them with a thank-you note and an easy discount on their second order.",
}


def _db():
    return SessionLocal()


@tool
def rfm_segments() -> dict:
    """RFM customer segmentation: customer counts + share per segment (Champions / Loyal / Promising / At Risk / Hibernating / New), plus repeat rate and AOV. Use for 'who are my best customers', 'break down my segments'."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        if r.get("status") == "OK":
            for s in r["segments"]:
                s["play"] = CAMPAIGN_PLAYS.get(s["segment"], "targeted offer")
        return r
    finally:
        db.close()


@tool
def category_demand() -> dict:
    """Top categories by units and revenue — the demand mix. Use for 'what is selling', 'demand trend'."""
    db = _db()
    try:
        return MarketingData.category_demand_trend(db)
    finally:
        db.close()


@tool
def customer_segment_mix() -> dict:
    """Customer segment mix breakdown for charts."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        return {"segments": r.get("segments", [])}
    finally:
        db.close()


@tool
def detect_low_retention() -> dict:
    """Flags a low repeat-purchase rate against a 20% reference."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        if r.get("status") != "OK" or r["repeat_rate_pct"] >= 20:
            return {"finding": None}
        return {
            "finding": {
                "category": "RETENTION",
                "severity": "HIGH" if r["repeat_rate_pct"] < 10 else "MEDIUM",
                "title": "Very few buyers are coming back to purchase again",
                "what_happened": f"Only {r['repeat_rate_pct']}% of {r['total_customers']:,} buyers have placed a second order (average order is {money(r['avg_order_value'])}).",
                "why_it_matters": "Finding brand new customers costs a lot more money than keeping the ones we already have. When buyers do not return, sales growth slows down.",
                "recommended_action": "Send a special discount offer to new buyers shortly after their first order to encourage them to buy again.",
                "evidence": fmt_evidence(
                    {k: r[k] for k in ("repeat_customers", "repeat_rate_pct", "total_customers")}
                ),
                "confidence": sample_confidence(r["total_customers"]),
                "data_status": "CALCULATED",
                "sample_count": r["total_customers"],
            }
        }
    finally:
        db.close()


@tool
def detect_churn_risk() -> dict:
    """Flags when 'At Risk' + 'Hibernating' together are >= 25% of the customer base."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        if r.get("status") != "OK":
            return {"finding": None}
        by = {s["segment"]: s for s in r["segments"]}
        lapsed = by.get("At Risk", {}).get("share_pct", 0) + by.get("Hibernating", {}).get("share_pct", 0)
        if lapsed < 25:
            return {"finding": None}
        return {
            "finding": {
                "category": "CHURN_RISK",
                "severity": "HIGH" if lapsed >= 45 else "MEDIUM",
                "title": "A large number of past buyers are stopping their purchases",
                "what_happened": f"Past buyers in 'At Risk' ({by.get('At Risk', {}).get('share_pct', 0)}%) and 'Hibernating' ({by.get('Hibernating', {}).get('share_pct', 0)}%) make up {round(lapsed, 1)}% of all customers.",
                "why_it_matters": "These people have bought from us before. Bringing them back costs much less than advertising to strangers.",
                "recommended_action": "Send a friendly 'we miss you' message with our best products and a small discount. Stop paying for internet ads to people who have stopped buying.",
                "evidence": fmt_evidence(
                    {"at_risk": by.get("At Risk"), "hibernating": by.get("Hibernating")}
                ),
                "confidence": sample_confidence(r["total_customers"]),
                "data_status": "CALCULATED",
                "sample_count": r["total_customers"],
            }
        }
    finally:
        db.close()


@tool
def detect_demand_concentration() -> dict:
    """Flags when the top category is >= 25% of units across the top-10 categories."""
    db = _db()
    try:
        cats = MarketingData.category_demand_trend(db)["categories"]
        if not cats:
            return {"finding": None}
        top = cats[0]
        share = round(top["units"] / max(1, sum(c["units"] for c in cats)) * 100, 1)
        if share < 25:
            return {"finding": None}
        return {
            "finding": {
                "category": "DEMAND_CONCENTRATION",
                "severity": "MEDIUM",
                "title": f"Most sales depend heavily on '{top['category']}'",
                "what_happened": f"'{top['category']}' accounts for {share}% of all items sold among the top {len(cats)} categories ({top['units']:,} items sold, bringing in {money(top['revenue'])}).",
                "why_it_matters": "It is great that this item sells well, but relying too much on one item is risky if supplies run low or interest drops.",
                "recommended_action": f"Put '{top['category']}' in front of new buyers, and offer bundle deals with other popular items so customers buy more variety.",
                "evidence": fmt_evidence(cats[:4]),
                "confidence": sample_confidence(top["units"]),
                "data_status": "CALCULATED",
            }
        }
    finally:
        db.close()


METRIC_TOOLS = [rfm_segments, category_demand, customer_segment_mix]
DETECTOR_TOOLS = [detect_low_retention, detect_churn_risk, detect_demand_concentration]


# ── Doc-grade marketing analytics ───────────────────────────────────────────
@tool
def marketing_analytics(metric: str) -> str:
    """Demand / category / geographic revenue analytics for marketing decisions.

    metric must be one of:
    - "category_revenue_share"    : each category's % contribution to total revenue, ranked
    - "top_revenue_categories"    : alias for category_revenue_share
    - "top_order_categories"      : top categories by number of orders
    - "best_seller_products"      : top 10 products by total revenue
    - "state_revenue_share"       : states by revenue contribution
    - "fastest_growing_categories": categories growing fastest in revenue YoY
    - "campaign_promoted_categories": top-promoted Olist categories (high volume + good reviews)
    - "high_sales_low_reviews"    : categories with above-avg volume but below-avg reviews
    - "sales_potential_categories": categories with strong volume and recent growth
    - "valuable_customer_segments": customer segments ranked by RFM value
    - "category_yoy_revenue"      : categories with the highest year-over-year revenue growth (2017→2018, %)
    - "aov_by_state"              : average order value by customer state; 5 highest
    - "state_order_share"         : customer states by % of total orders; combined share of the top ones
    - "volume_vs_reviews"         : alias for high_sales_low_reviews
    - "revenue_per_order_cat"     : revenue per order for each category; highest first
    - "volume_revenue_growth"     : categories that grew BOTH order volume and revenue 2017→2018 (both %)
    - "top10_products_share"      : % of total revenue generated by the top 10% of products
    - "growth_gap"                : categories with high order growth but low revenue growth
    - "state_score"               : states ranked by combined volume+revenue+AOV score
    - "opportunity_rank"          : 5 strongest marketing opportunities scored transparently
    - "campaign_recommendation_5" : data-driven recommendation of 5 categories for marketing campaigns
    - "dataco_marketing"          : DataCo dataset market/segment/region breakdown
    """
    import statistics as st
    from collections import defaultdict

    from app.agents._shared import simulated_clock
    from app.models.olist import CategoryTranslation, Customer, Order, OrderItem, OrderReview, Product

    db = _db()
    try:
        clock = simulated_clock(db)
        rows = (
            db.query(
                Product.product_category_name,
                Product.product_id,
                Customer.customer_state,
                Order.order_id,
                period_bucket(db, Order.order_purchase_timestamp, "%Y"),
                OrderItem.price,
                OrderItem.freight_value,
            )
            .join(Order, Order.order_id == OrderItem.order_id)
            .join(Product, Product.product_id == OrderItem.product_id)
            .join(Customer, Customer.customer_id == Order.customer_id)
            .filter(Order.order_purchase_timestamp <= clock)
            .all()
        )
        if not rows:
            return "No marketing data observed yet."
        cat_en = {
            r[0]: r[1]
            for r in db.query(
                CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english
            ).all()
        }

        def disp(c):
            return cat_en.get(c, (c or "unknown")).replace("_", " ").title()

        cat_revenue: dict = defaultdict(float)
        cat_orders: dict = defaultdict(set)
        cat_year_rev: dict = defaultdict(lambda: defaultdict(float))
        cat_year_orders: dict = defaultdict(lambda: defaultdict(set))
        defaultdict(list)
        prod_revenue: dict = defaultdict(float)
        state_rev: dict = defaultdict(float)
        state_orders: dict = defaultdict(set)
        state_aov_rev: dict = defaultdict(float)
        total_revenue = 0.0

        for cat, pid, state, oid, yr, price, freight in rows:
            v = float(price or 0) + float(freight or 0)
            cat_revenue[cat] += v
            cat_orders[cat].add(oid)
            cat_year_rev[cat][yr] += v
            cat_year_orders[cat][yr].add(oid)
            prod_revenue[pid] += v
            state_rev[state] += v
            state_orders[state].add(oid)
            state_aov_rev[state] += v
            total_revenue += v

        # category avg review scores (for review-aware metrics)
        cat_review = defaultdict(list)
        review_rows = (
            db.query(Product.product_category_name, OrderReview.review_score)
            .join(OrderItem, OrderItem.product_id == Product.product_id)
            .join(OrderReview, OrderReview.order_id == OrderItem.order_id)
            .filter(OrderReview.review_score.isnot(None))
            .all()
        )
        for cat, score in review_rows:
            if cat and score is not None:
                cat_review[cat].append(int(score))

        def cat_avg_review(cat):
            v = cat_review.get(cat) or []
            return st.mean(v) if v else None

        all_scores = [s for v in cat_review.values() for s in v]
        overall_avg_score = st.mean(all_scores) if all_scores else None
        overall_avg_orders = st.mean(len(o) for o in cat_orders.values()) if cat_orders else 0

        # Handle aliases
        if metric in ("top_revenue_categories", "category_revenue_share"):
            parts = ", ".join(
                f"{disp(c)} {v / total_revenue * 100:.1f}%"
                for c, v in sorted(cat_revenue.items(), key=lambda kv: -kv[1])[:10]
            )
            return f"Category revenue contribution (top 10 of R${total_revenue:,.0f} total): {parts}"
        if metric == "top_order_categories":
            ranked = sorted(cat_orders.items(), key=lambda kv: -len(kv[1]))[:10]
            total_orders = len({oid for o in cat_orders.values() for oid in o}) or 1
            parts = ", ".join(
                f"{disp(c)}: {len(o):,} orders ({len(o)/total_orders*100:.1f}%)" for c, o in ranked
            )
            return f"Top 10 categories by number of orders: {parts}"
        if metric == "best_seller_products":
            ranked = sorted(prod_revenue.items(), key=lambda kv: -kv[1])[:10]
            parts = "\n".join(f"  {i+1}. Product {pid}: R${rev:,.0f}" for i, (pid, rev) in enumerate(ranked))
            return f"Top 10 products by total revenue:\n{parts}"
        if metric == "state_revenue_share":
            max_rev = sum(state_rev.values()) or 1
            ranked = sorted(state_rev.items(), key=lambda kv: -kv[1])[:10]
            parts = ", ".join(f"{s}: R${v:,.0f} ({v/max_rev*100:.1f}%)" for s, v in ranked)
            return f"Top 10 states by revenue: {parts}"
        if metric == "fastest_growing_categories":
            diffs = []
            for cat, yr in cat_year_rev.items():
                a, b = yr.get("2017", 0.0), yr.get("2018", 0.0)
                if a > 0:
                    diffs.append((cat, a, b, (b - a) / a * 100))
            diffs.sort(key=lambda d: -d[3])
            parts = ", ".join(f"{disp(c)} +{g:.0f}%" for c, a, b, g in diffs[:10])
            return "Fastest growing categories by revenue (2017→2018): " + (parts or "no comparable data")
        if metric == "campaign_promoted_categories":
            # High volume + good reviews = good for promotion
            out = []
            for cat, orders in cat_orders.items():
                avg_s = cat_avg_review(cat)
                if (
                    avg_s is not None
                    and len(orders) >= overall_avg_orders
                    and avg_s >= (overall_avg_score or 4.0)
                ):
                    out.append((cat, len(orders), avg_s, cat_revenue[cat]))
            out.sort(key=lambda x: -x[1])
            parts = ", ".join(
                f"{disp(c)} ({n:,} orders, {s:.2f}/5 avg review, R${r:,.0f} revenue)"
                for c, n, s, r in out[:8]
            )
            return "Top categories recommended for promotion (high volume + good reviews): " + (
                parts or "none found"
            )
        if metric in ("high_sales_low_reviews", "volume_vs_reviews"):
            out = []
            for cat, orders in cat_orders.items():
                avg_s = cat_avg_review(cat)
                if avg_s is not None and len(orders) > overall_avg_orders and avg_s < overall_avg_score:
                    out.append((cat, len(orders), avg_s))
            out.sort(key=lambda x: -x[1])
            parts = ", ".join(
                f"{disp(c)} ({n} orders, {s:.2f}/5 vs {overall_avg_score:.2f} avg)" for c, n, s in out[:8]
            )
            return "Above-average volume but below-average review score: " + (parts or "none")
        if metric == "sales_potential_categories":
            # Categories growing AND with above-avg orders -> strong sales potential
            out = []
            for cat in list(cat_revenue.keys()):
                ro = cat_year_orders[cat].get("2017")
                rn = cat_year_orders[cat].get("2018")
                if ro and rn and len(ro) > 0:
                    og = (len(rn) - len(ro)) / len(ro) * 100
                    if og > 0 and len(cat_orders[cat]) >= overall_avg_orders:
                        out.append((cat, og, len(cat_orders[cat]), cat_revenue[cat]))
            out.sort(key=lambda x: -(x[1] + x[2] / 1000))
            parts = ", ".join(
                f"{disp(c)} (+{g:.0f}% orders growth, {n:,} total orders, R${r:,.0f} revenue)"
                for c, g, n, r in out[:8]
            )
            return "Categories with strong sales potential (growth + volume): " + (parts or "none found")
        if metric == "valuable_customer_segments":
            # Pull RFM segments from data layer
            from app.agents.marketing.data_layer import MarketingData

            rfm = MarketingData.rfm(db)
            if rfm.get("status") != "OK":
                return "RFM data not available."
            segs = sorted(rfm["segments"], key=lambda s: -s.get("customers", 0))
            parts = "\n".join(
                f"  {i+1}. {s['segment']}: {s['customers']:,} customers ({s['share_pct']}%), "
                f"avg recency {s.get('avg_recency_days', 'N/A')}d"
                for i, s in enumerate(segs)
            )
            return (
                f"Customer segments by value (RFM analysis, {rfm['total_customers']:,} total customers):\n{parts}\n"
                f"Repeat rate: {rfm['repeat_rate_pct']}%, Avg order value: R${rfm['avg_order_value']:,.2f}"
            )
        if metric == "category_yoy_revenue":
            diffs = []
            for cat, yr in cat_year_rev.items():
                a, b = yr.get("2017", 0.0), yr.get("2018", 0.0)
                if a > 0:
                    diffs.append((cat, a, b, (b - a) / a * 100))
            diffs.sort(key=lambda d: -d[3])
            parts = ", ".join(f"{disp(c)} +{g:.0f}%" for c, a, b, g in diffs[:8])
            return "Highest YoY revenue growth 2017→2018: " + (parts or "no comparable data")
        if metric == "aov_by_state":
            rows2 = sorted(
                ((s, state_aov_rev[s] / max(1, len(state_orders[s]))) for s in state_orders),
                key=lambda x: -x[1],
            )
            parts = ", ".join(f"{s}: R${a:,.2f}" for s, a in rows2[:5])
            return f"Five states with the highest average order value: {parts}"
        if metric == "state_order_share":
            total_orders = len({oid for o in cat_orders.values() for oid in o}) or 1
            rows2 = sorted(((s, len(o)) for s, o in state_orders.items()), key=lambda x: -x[1])
            top5 = rows2[:5]
            combined = sum(n for _, n in top5) / total_orders * 100
            parts = ", ".join(f"{s} {n / total_orders * 100:.1f}%" for s, n in top5)
            return (
                f"States by order share: {parts} — top 5 combined {combined:.1f}% of {total_orders:,} orders"
            )
        if metric == "revenue_per_order_cat":
            rows2 = sorted(
                ((c, cat_revenue[c] / max(1, len(cat_orders[c]))) for c in cat_revenue),
                key=lambda x: -x[1],
            )
            parts = ", ".join(f"{disp(c)}: R${v:,.2f}" for c, v in rows2[:8])
            return f"Revenue per order by category (highest first): {parts}"
        if metric == "volume_revenue_growth":
            out = []
            for cat in cat_revenue:
                ro, rn = cat_year_orders[cat].get("2017"), cat_year_orders[cat].get("2018")
                rv, rn2 = cat_year_rev[cat].get("2017", 0.0), cat_year_rev[cat].get("2018", 0.0)
                if ro and rn and rv > 0:
                    og = (len(rn) - len(ro)) / len(ro) * 100
                    rg = (rn2 - rv) / rv * 100
                    if og > 0 and rg > 0:
                        out.append((cat, og, rg))
            out.sort(key=lambda x: -(x[1] + x[2]))
            parts = ", ".join(f"{disp(c)} (orders +{og:.0f}%, revenue +{rg:.0f}%)" for c, og, rg in out[:8])
            return "Categories growing in BOTH volume and revenue 2017→2018: " + (parts or "none")
        if metric == "top10_products_share":
            total = total_revenue or 1
            ranked = sorted(prod_revenue.values(), reverse=True)
            n = max(1, len(ranked) // 10)
            share = sum(ranked[:n]) / total * 100
            return f"Top 10% of products generate {share:.1f}% of total revenue (R${sum(ranked[:n]):,.0f} of R${total:,.0f})."
        if metric == "growth_gap":
            out = []
            for cat in cat_revenue:
                ro = cat_year_orders[cat].get("2017")
                rn = cat_year_orders[cat].get("2018")
                rv = cat_year_rev[cat].get("2017", 0.0)
                rn2 = cat_year_rev[cat].get("2018", 0.0)
                if ro and rn and rv > 0:
                    og = (len(rn) - len(ro)) / len(ro) * 100
                    rg = (rn2 - rv) / rv * 100
                    if og >= 20 and rg < og / 2:
                        out.append((cat, og, rg))
            out.sort(key=lambda x: -(x[1] - x[2]))
            parts = ", ".join(
                f"{disp(c)} (orders +{og:.0f}% but revenue only +{rg:.0f}% — new buyers are buying cheaper items)"
                for c, og, rg in out[:6]
            )
            return "High order growth but lagging revenue growth: " + (parts or "none")
        if metric == "state_score":
            total_orders = len({oid for o in cat_orders.values() for oid in o}) or 1
            max_rev = max(state_rev.values()) if state_rev else 1
            max_vol = max(len(o) for o in state_orders.values()) if state_orders else 1
            scored = []
            for s, orders in state_orders.items():
                aov = state_aov_rev[s] / max(1, len(orders))
                vol = len(orders) / max_vol
                rev = state_rev[s] / max_rev
                score = 0.4 * vol + 0.4 * rev + 0.2 * (aov / 200.0)
                scored.append((s, score, vol, rev, aov))
            scored.sort(key=lambda x: -x[1])
            parts = "; ".join(
                f"{s}: score {sc:.2f} = 0.4*vol({v:.2f}) + 0.4*rev({r:.2f}) + 0.2*AOV(R${a:,.0f})"
                for s, sc, v, r, a in scored[:5]
            )
            return f"State ranking by combined score (volume 40% + revenue 40% + AOV 20%): {parts}"
        if metric == "opportunity_rank":
            scored = []
            for cat, cat_rev in cat_revenue.items():
                ro = cat_year_orders[cat].get("2017")
                rn = cat_year_orders[cat].get("2018")
                growth = ((len(rn) - len(ro)) / len(ro) * 100) if (ro and rn) else 0.0
                rev_share = cat_rev / total_revenue * 100
                aov = cat_rev / max(1, len(cat_orders[cat]))
                avg_s = cat_avg_review(cat)
                score = (
                    0.35 * min(growth, 100)
                    + 0.30 * min(rev_share * 5, 100)
                    + 0.20 * min(aov / 3.0, 100)
                    + 0.15 * ((avg_s or 3.0) / 5 * 100)
                )
                scored.append((cat, score, growth, rev_share, aov, avg_s))
            scored.sort(key=lambda x: -x[1])
            parts = "; ".join(
                f"{disp(c)} (score {sc:.1f} = 35%*growth({g:.0f}%) + 30%*rev-share({rs:.1f}%) + 20%*AOV(R${a:,.0f}) + 15%*reviews({s or 'n/a'}/5))"
                for c, sc, g, rs, a, s in scored[:5]
            )
            return f"Top 5 marketing opportunities (transparent weighted score): {parts}"
        if metric == "campaign_recommendation_5":
            # Strong test: recommend 5 categories for marketing campaigns
            scored = []
            for cat, cat_rev in cat_revenue.items():
                if len(cat_orders[cat]) < 50:
                    continue
                ro = cat_year_orders[cat].get("2017")
                rn = cat_year_orders[cat].get("2018")
                growth = ((len(rn) - len(ro)) / len(ro) * 100) if (ro and rn and len(ro) > 0) else 0.0
                rev_share = cat_rev / total_revenue * 100
                aov = cat_rev / max(1, len(cat_orders[cat]))
                avg_s = cat_avg_review(cat)
                # Balanced score: growth + market size + quality signal
                score = (
                    0.3 * min(growth, 100)
                    + 0.35 * min(rev_share * 5, 100)
                    + 0.2 * min(aov / 3.0, 100)
                    + 0.15 * ((avg_s or 3.0) / 5 * 100)
                )
                scored.append((cat, score, growth, rev_share, aov, avg_s, cat_rev, len(cat_orders[cat])))
            scored.sort(key=lambda x: -x[1])
            top5 = scored[:5]
            if not top5:
                return "Not enough category data for campaign recommendations."
            rec_parts = []
            for i, (cat, sc, g, rs, aov, avg_s, rev, n_orders) in enumerate(top5):
                reason_parts = []
                if g > 30:
                    reason_parts.append(f"rapid growth (+{g:.0f}% YoY)")
                if rs > 5:
                    reason_parts.append(f"large revenue share ({rs:.1f}%)")
                if aov > 200:
                    reason_parts.append(f"high AOV (R${aov:,.0f})")
                if avg_s and avg_s >= 4.0:
                    reason_parts.append(f"strong reviews ({avg_s:.2f}/5)")
                reason = "; ".join(reason_parts) or "balanced performance"
                rec_parts.append(
                    f"  {i+1}. **{disp(cat)}** (score {sc:.1f}): {n_orders:,} orders, "
                    f"R${rev:,.0f} revenue ({rs:.1f}% share), AOV R${aov:,.0f}. "
                    f"Rationale: {reason}."
                )
            return (
                "Data-driven campaign recommendation — 5 Olist categories for marketing investment:\n"
                + "\n".join(rec_parts)
                + "\n\nMethodology: Weighted score = 30% YoY growth + 35% revenue share + 20% AOV + 15% avg review."
            )
        if metric == "dataco_marketing":
            from sqlalchemy import func as _f

            from app.models.dataco import DataCoOrder as _DCO, DataCoOrderItem as _DCI

            # Market breakdown
            market_rows = (
                db.query(_DCO.market, _DCO.customer_segment, _f.count(_DCO.order_id))
                .group_by(_DCO.market, _DCO.customer_segment)
                .order_by(_f.count(_DCO.order_id).desc())
                .limit(20)
                .all()
            )
            # Regional revenue
            region_rows = (
                db.query(_DCO.order_region, _f.sum(_DCI.sales), _f.count(_DCI.order_item_id))
                .join(_DCI, _DCI.order_id == _DCO.order_id)
                .group_by(_DCO.order_region)
                .order_by(_f.sum(_DCI.sales).desc())
                .limit(10)
                .all()
            )
            if not market_rows and not region_rows:
                return "No DataCo marketing data available."
            market_parts = "\n".join(
                f"  {market or 'Unknown'} / {seg or 'Unknown'}: {cnt:,} orders"
                for market, seg, cnt in market_rows[:10]
            )
            region_parts = "\n".join(
                f"  {reg or 'Unknown'}: ${rev:,.0f} revenue, {cnt:,} items" for reg, rev, cnt in region_rows
            )
            return (
                f"DataCo marketing breakdown:\n\nMarket & Segment distribution (top 10):\n{market_parts}"
                f"\n\nRevenue by region (top 10):\n{region_parts}"
            )
        return (
            "Unknown metric. Use one of: category_revenue_share, top_revenue_categories, top_order_categories, "
            "best_seller_products, state_revenue_share, fastest_growing_categories, campaign_promoted_categories, "
            "high_sales_low_reviews, sales_potential_categories, valuable_customer_segments, "
            "category_yoy_revenue, aov_by_state, state_order_share, volume_vs_reviews, revenue_per_order_cat, "
            "volume_revenue_growth, top10_products_share, growth_gap, state_score, opportunity_rank, "
            "campaign_recommendation_5, dataco_marketing"
        )
    except Exception as exc:
        return f"❌ Analytics failed: {exc}"
    finally:
        db.close()


LOOKUP_TOOLS = [marketing_analytics]
