"""
LangChain tools for the customer support ReAct agent. Every tool reads the same
transaction database as the other agents, source-aware to the simulated clock.
Used by `create_react_agent` when a chat model is configured.
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.agents.customer.tools import CustomerSupportTools
from app.agents.entity_resolver import entity_resolver
from app.agents.orders.tools import OrdersTools
from app.database.session import SessionLocal
from app.models.olist import CategoryTranslation, Customer, Order, OrderItem, OrderReview, Product, Seller


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by its ID. Returns status, total, items, tracking number, customer city/state, and key dates."""
    detail = OrdersTools.lookup_order(order_id)
    if not detail:
        # Cross-table fallback: if user provided a customer, seller, product, or review ID instead
        fallback = entity_resolver.resolve_entity(order_id)
        if fallback.get("status") == "FOUND":
            return fallback.get("summary", f"Found entity of type {fallback.get('entity_type')}.")
        return f"Order '{order_id}' was not found."
    items = (
        "; ".join(f"{it.product_name} x{it.quantity} (R${it.price:.2f})" for it in detail.items)
        or "fulfilment package"
    )
    return (
        f"Order {detail.order_id}: status {detail.status.upper()}, total R${detail.total:.2f}, "
        f"placed {(detail.purchase_timestamp or '')[:10]}, delivered {(detail.delivered_customer_date or 'not yet')[:10]}, "
        f"tracking {detail.tracking_number}, ships to {detail.customer_city or '?'}, {detail.customer_state or '?'}. Items: {items}."
    )


@tool
def lookup_customer(customer_id: str) -> str:
    """Look up customer profile, location, lifetime spend, and all associated orders using customer_id or customer_unique_id (or DataCo customer ID)."""
    res = entity_resolver.resolve_entity(customer_id)
    if res.get("status") == "FOUND" and res.get("entity_type") == "customer":
        return res.get("summary", "Customer found.")
    # If not directly matched as customer, check if it resolves to any entity
    if res.get("status") == "FOUND":
        return f"ID '{customer_id}' is a {res.get('entity_type')}, not a customer:\n" + res.get("summary", "")
    return f"Customer '{customer_id}' was not found in database records."


@tool
def lookup_seller(seller_id: str) -> str:
    """Look up seller location, order items fulfilled, and catalog items using seller_id."""
    res = entity_resolver.resolve_entity(seller_id)
    if res.get("status") == "FOUND" and res.get("entity_type") == "seller":
        return res.get("summary", "Seller found.")
    if res.get("status") == "FOUND":
        return f"ID '{seller_id}' is a {res.get('entity_type')}, not a seller:\n" + res.get("summary", "")
    return f"Seller '{seller_id}' was not found in database records."


@tool
def lookup_review(review_id: str) -> str:
    """Look up customer review details, star rating, comment title and message, and linked order using review_id."""
    res = entity_resolver.resolve_entity(review_id)
    if res.get("status") == "FOUND" and res.get("entity_type") == "review":
        return res.get("summary", "Review found.")
    if res.get("status") == "FOUND":
        return f"ID '{review_id}' is a {res.get('entity_type')}, not a review:\n" + res.get("summary", "")
    return f"Review '{review_id}' was not found in database records."


@tool
def resolve_unknown_id(entity_id: str) -> str:
    """Universal entity lookup: inspects orders, customers, products, sellers, and reviews to identify what entity this ID belongs to and returns all linked data."""
    res = entity_resolver.resolve_entity(entity_id)
    return res.get("summary", f"No record matching ID '{entity_id}' found.")


@tool
def track_shipment(order_id: str) -> str:
    """Get carrier, status, ETA, and dispatch milestones for an order's shipment."""
    r = OrdersTools.track_shipment(order_id)
    if r.status == "NOT_FOUND":
        return f"No shipment found for order '{order_id}'."
    events = (
        "; ".join(f"{e.status} @ {e.location} ({e.timestamp[:10]})" for e in r.events)
        or "registered with carrier"
    )
    return f"Carrier {r.carrier}, tracking {r.tracking_number}, status {r.status}, ETA {(r.estimated_delivery or 'on schedule')[:10]}. Milestones: {events}."


@tool
def check_return_eligibility(order_id: str) -> str:
    """Check whether an order is within the 30-day return window (based on delivery date vs the current clock)."""
    r = OrdersTools.check_return_eligibility(order_id)
    return f"{'ELIGIBLE' if r.is_eligible else 'INELIGIBLE'} ({r.status}): {r.message}"


@tool
def get_return_policy() -> str:
    """Return the store's return & refund policy."""
    return OrdersTools.get_return_policy()


@tool
def initiate_return(order_id: str, reason: str) -> str:
    """Create an RMA for an eligible order. Only call after check_return_eligibility says ELIGIBLE."""
    elig = OrdersTools.check_return_eligibility(order_id)
    if not elig.is_eligible:
        return f"Cannot create an RMA — order {order_id} is {elig.status}: {elig.message}"
    rma = OrdersTools.initiate_return(order_id, reason=reason or "Customer requested return")
    return f"RMA {rma.rma_number} created for order {order_id}: R${rma.refund_amount:.2f} refund. {rma.instructions}"


@tool
def billing_details(order_id: str) -> str:
    """Explain the charge on an order using real payment records (amount, method, installments)."""
    text, _ = CustomerSupportTools.billing_lookup(order_id)
    return text


@tool
def product_search(keyword: str) -> str:
    """Search the catalog by product ID or a category keyword (English or Portuguese). Returns matches with price and units sold."""
    text, _ = CustomerSupportTools.product_info(keyword)
    return text


@tool
def store_health() -> str:
    """High-level store operations snapshot: order volume, fulfilment %, delay %, SLA."""
    text, _ = CustomerSupportTools.pipeline_snapshot()
    return text


CUSTOMER_TOOLS = [
    lookup_order,
    lookup_customer,
    lookup_seller,
    lookup_review,
    resolve_unknown_id,
    track_shipment,
    check_return_eligibility,
    get_return_policy,
    initiate_return,
    billing_details,
    product_search,
    store_health,
]


# ── Doc-grade customer-experience analytics ─────────────────────────────────
@tool
def customer_experience_analytics(metric: str) -> str:
    """Review-score / complaint / delivery-experience analytics (customer-facing CX).

    metric must be one of:
    - "score_distribution"  : % of orders receiving each review score 1-5
    - "delivered_vs_late"   : average review score for delivered-on-time vs delivered-late orders
    - "late_low_score_pct"  : % of late-delivered orders rated 1 or 2
    - "category_low_review" : % of low-rated (1-2) reviews per category; highest first
    - "state_low_scores"    : average review score by customer state; 5 lowest
    - "late_vs_ontime"      : average review score of customers whose orders were late vs on time
    - "late_1_2_pct"        : % of customers who experienced a late delivery and then rated 1 or 2
    - "seller_poor_reviews" : sellers (>=20 reviews) with the highest proportion of poor reviews
    - "review_response_time": average hours between review creation and the review answer
    - "delay_review_hotspots": categories where delays and poor reviews co-occur above the dataset average
    - "issue_rank"          : CX issues most strongly associated with low scores, quantified and ranked
    """
    import statistics as st
    from collections import defaultdict

    from app.agents._shared import simulated_clock

    db = SessionLocal()
    try:
        clock = simulated_clock(db)
        rows = (
            db.query(
                Order.order_id,
                Order.order_delivered_customer_date,
                Order.order_estimated_delivery_date,
                OrderReview.review_score,
                OrderReview.review_creation_date,
                OrderReview.review_answer_timestamp,
                Customer.customer_state,
                Seller.seller_id,
                Product.product_category_name,
            )
            .join(OrderReview, OrderReview.order_id == Order.order_id)
            .join(Customer, Customer.customer_id == Order.customer_id)
            .outerjoin(OrderItem, OrderItem.order_id == Order.order_id)
            .outerjoin(Seller, Seller.seller_id == OrderItem.seller_id)
            .outerjoin(Product, Product.product_id == OrderItem.product_id)
            .filter(Order.order_purchase_timestamp <= clock, OrderReview.review_score.isnot(None))
            .all()
        )
        if not rows:
            return "No review data observed yet."
        cat_en = {
            r[0]: r[1]
            for r in db.query(
                CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english
            ).all()
        }

        def disp(c):
            return cat_en.get(c, (c or "unknown")).replace("_", " ").title()

        seen_orders = set()
        scores = []
        late_scores, ontime_scores = [], []
        cat_total: dict = defaultdict(int)
        cat_low: dict = defaultdict(int)
        state_scores: dict = defaultdict(list)
        seller_total: dict = defaultdict(int)
        seller_low: dict = defaultdict(int)
        response_hours = []
        cat_late_low: dict = defaultdict(int)
        cat_late_total: dict = defaultdict(int)

        for oid, deliv, est, score, created, answered, state, sid, cat in rows:
            if oid in seen_orders:
                # multi-item orders: aggregate seller/category stats only
                if sid and cat:
                    seller_total[sid] += 0
                continue
            seen_orders.add(oid)
            scores.append(int(score))
            is_late = bool(deliv and est and deliv > est)
            if is_late:
                late_scores.append(int(score))
            else:
                ontime_scores.append(int(score))
            if state:
                state_scores[state].append(int(score))
            if created and answered:
                response_hours.append((answered - created).total_seconds() / 3600.0)

        # category/seller aggregates need item rows; do them separately per order
        item_rows = (
            db.query(OrderItem.order_id, Seller.seller_id, Product.product_category_name)
            .join(Seller, Seller.seller_id == OrderItem.seller_id)
            .join(Product, Product.product_id == OrderItem.product_id)
            .all()
        )
        order_sellers: dict = defaultdict(set)
        order_cats: dict = defaultdict(set)
        for oid, sid, cat in item_rows:
            if sid:
                order_sellers[oid].add(sid)
            if cat:
                order_cats[oid].add(cat)

        order_info = {}
        for oid, deliv, est, score, _created, _answered, _state, _sid, _cat in rows:
            if oid in order_info:
                continue
            order_info[oid] = (bool(deliv and est and deliv > est), int(score))

        for oid, (is_late, score) in order_info.items():
            for sid in order_sellers.get(oid, set()):
                seller_total[sid] += 1
                if score <= 2:
                    seller_low[sid] += 1
            for cat in order_cats.get(oid, set()):
                cat_total[cat] += 1
                if score <= 2:
                    cat_low[cat] += 1
                if is_late:
                    cat_late_total[cat] += 1
                    if score <= 2:
                        cat_late_low[cat] += 1

        overall_low_pct = sum(1 for s in scores if s <= 2) / max(1, len(scores))

        if metric == "score_distribution":
            total = len(scores) or 1
            parts = ", ".join(f"{k}★ {scores.count(k) / total * 100:.1f}%" for k in (1, 2, 3, 4, 5))
            return f"Review score distribution across {total:,} reviewed orders: {parts}"
        if metric in ("delivered_vs_late", "late_vs_ontime"):
            avg_late = st.mean(late_scores) if late_scores else None
            avg_ok = st.mean(ontime_scores) if ontime_scores else None
            return (
                f"Average review score — delivered late: {avg_late:.2f}/5 ({len(late_scores):,} orders) "
                f"vs on-time/early: {avg_ok:.2f}/5 ({len(ontime_scores):,} orders)."
            )
        if metric == "late_low_score_pct":
            low = sum(1 for s in late_scores if s <= 2)
            return f"{low:,} of {len(late_scores):,} late-delivered orders ({low / max(1, len(late_scores)) * 100:.1f}%) were rated 1 or 2."
        if metric == "category_low_review":
            rows2 = sorted(
                ((c, cat_low[c] / cat_total[c] * 100) for c in cat_total if cat_total[c] >= 20),
                key=lambda x: -x[1],
            )
            parts = ", ".join(f"{disp(c)} {p:.1f}%" for c, p in rows2[:8])
            return "Categories with the highest share of low-rated (1-2) reviews: " + (parts or "none")
        if metric == "state_low_scores":
            rows2 = sorted(
                ((s, st.mean(v)) for s, v in state_scores.items() if len(v) >= 20), key=lambda x: x[1]
            )
            parts = ", ".join(f"{s}: {v:.2f}/5" for s, v in rows2[:5])
            return "Five states with the lowest average review score: " + (parts or "none")
        if metric == "late_1_2_pct":
            late_n = len(late_scores) or 1
            low = sum(1 for s in late_scores if s <= 2)
            return f"{low / late_n * 100:.1f}% of customers who experienced a late delivery then rated it 1 or 2 ({low:,} of {late_n:,})."
        if metric == "seller_poor_reviews":
            rows2 = []
            for sid, n in seller_total.items():
                if n >= 20 and seller_low[sid]:
                    rows2.append((sid, seller_low[sid] / n * 100, n))
            rows2.sort(key=lambda x: -x[1])
            parts = ", ".join(f"#{sid[:8]} {p:.0f}% of {n}" for sid, p, n in rows2[:6])
            return "Sellers with the highest poor-review proportion (min 20 reviews): " + (parts or "none")
        if metric == "review_response_time":
            if not response_hours:
                return "No answered reviews observed yet."
            return f"Average time between review creation and answer: {st.mean(response_hours):.1f} hours across {len(response_hours):,} answered reviews."
        if metric == "delay_review_hotspots":
            out = []
            for cat, late_n in cat_late_total.items():
                if late_n < 20:
                    continue
                co = cat_late_low[cat] / late_n * 100
                base = overall_low_pct * 100
                if co > base:
                    out.append((cat, co, base))
            out.sort(key=lambda x: -x[1])
            parts = ", ".join(f"{disp(c)} ({co:.1f}% vs {b:.1f}% avg)" for c, co, b in out[:6])
            return "Categories where delay + poor reviews co-occur above average: " + (parts or "none")
        if metric == "issue_rank":
            late_avg = st.mean(late_scores) if late_scores else None
            ok_avg = st.mean(ontime_scores) if ontime_scores else None
            gaps = []
            if late_avg is not None and ok_avg is not None:
                gaps.append(
                    (
                        "Late delivery",
                        (ok_avg - late_avg) * len(late_scores),
                        f"{(ok_avg - late_avg):.2f}-star drop across {len(late_scores):,} late orders",
                    )
                )
            worst_cat = None
            cat_rows = [(c, cat_low[c] / cat_total[c] * 100) for c in cat_total if cat_total[c] >= 20]
            if cat_rows:
                worst_cat = max(cat_rows, key=lambda x: x[1])
                gaps.append(
                    (
                        "Worst category " + disp(worst_cat[0]),
                        (worst_cat[1] - overall_low_pct * 100) / 100 * cat_total[worst_cat[0]],
                        f"{worst_cat[1]:.1f}% low-rated vs {overall_low_pct * 100:.1f}% overall",
                    )
                )
            gaps.sort(key=lambda x: -x[1])
            parts = "; ".join(f"{i+1}. {name} — {detail}" for i, (name, _, detail) in enumerate(gaps[:4]))
            return "CX issues ranked by impact on review scores: " + (parts or "no significant issues found")
        if metric == "dataco_delivery_risk":
            from sqlalchemy import func as _f

            from app.models.dataco import DataCoOrder as _DCO

            risk_rows = (
                db.query(
                    _DCO.delivery_status,
                    _DCO.shipping_mode,
                    _f.count(_DCO.order_id),
                    _f.avg(_DCO.late_delivery_risk),
                    _f.avg(_DCO.days_for_shipping_real),
                    _f.avg(_DCO.days_for_shipment_scheduled),
                )
                .group_by(_DCO.delivery_status, _DCO.shipping_mode)
                .order_by(_f.count(_DCO.order_id).desc())
                .limit(12)
                .all()
            )
            if not risk_rows:
                return "No DataCo delivery risk data available."
            total_dc = db.query(_f.count(_DCO.order_id)).scalar() or 1
            late_risk_total = db.query(_f.sum(_DCO.late_delivery_risk)).scalar() or 0
            parts = "\n".join(
                f"  {status or 'Unknown'} / {mode or 'Unknown'}: {cnt:,} orders, "
                f"avg late risk {risk*100:.1f}%, "
                f"actual shipping {act:.1f}d vs scheduled {sched:.1f}d"
                for status, mode, cnt, risk, act, sched in risk_rows
            )
            return (
                f"DataCo delivery risk summary ({total_dc:,} total orders, "
                f"{late_risk_total/total_dc*100:.1f}% flagged as late risk):\n{parts}"
            )
        return (
            "Unknown metric. Use one of: score_distribution, delivered_vs_late, late_low_score_pct, "
            "category_low_review, state_low_scores, late_vs_ontime, late_1_2_pct, seller_poor_reviews, "
            "review_response_time, delay_review_hotspots, issue_rank, dataco_delivery_risk"
        )
    except Exception as exc:
        return f"❌ Analytics failed: {exc}"
    finally:
        db.close()


CUSTOMER_TOOLS.append(customer_experience_analytics)
