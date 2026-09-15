"""
Inventory Agent Tools.
Empirical inventory data queries, sales analysis, Reorder Point (ROP),
EOQ calculations, and stock adjustments.
"""
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from langchain_core.tools import tool

from app.models.olist import Product, OrderItem, Order, CategoryTranslation
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.agents.inventory.schemas import (
    InventoryProduct,
    ReorderRecommendation,
    SalesAnalysis,
    InventoryAlert,
    InventoryAction,
    ToolCallRecord,
    InventoryAgentMetrics,
)

logger = logging.getLogger(__name__)


# Olist / DataCo carry no warehouse feed, so the on-hand position is MODELLED from
# real demand: a store that sells D units/day typically holds a cover window of
# stock. The cover window is the one documented assumption; the demand it multiplies
# is measured from actual order_items. `data_status` on every inventory output is
# therefore MODELLED, never OBSERVED.
_COVER_DAYS_BY_TURNOVER = {"fast": 12, "medium": 22, "slow": 40}
_DEFAULT_LEAD_TIME = 7


_RECENT_WINDOW_DAYS = 180


# Full-dataset demand stats depend only on order_items + the simulated clock, so
# they are cached per clock value — query_products, reorder recommendations,
# alerts and trends all funnel through here and were re-running the same two
# grouped SQL scans over every product on every call (the agent's multi-second
# load time).
_DEMAND_CACHE: Dict[Any, Dict[str, Dict[str, float]]] = {}


def _demand_stats(db: Session, product_ids: Optional[List[str]] = None) -> Dict[str, Dict[str, float]]:
    """Real per-product demand: total units, units in the recent window, first/last sale."""
    from app.agents._shared import simulated_clock

    clock = simulated_clock(db)
    cache_key = ("all", str(clock))
    if cache_key in _DEMAND_CACHE:
        full = _DEMAND_CACHE[cache_key]
        if product_ids is None:
            return full
        wanted = {str(p) for p in product_ids}
        return {pid: stats for pid, stats in full.items() if pid in wanted}

    cutoff = clock - timedelta(days=_RECENT_WINDOW_DAYS)

    q = (
        db.query(
            OrderItem.product_id,
            func.count(OrderItem.order_item_id),
            func.min(Order.order_purchase_timestamp),
            func.max(Order.order_purchase_timestamp),
        )
        .join(Order, Order.order_id == OrderItem.order_id)
        .filter(Order.order_purchase_timestamp <= clock)
        .group_by(OrderItem.product_id)
    )

    recent_q = (
        db.query(OrderItem.product_id, func.count(OrderItem.order_item_id))
        .join(Order, Order.order_id == OrderItem.order_id)
        .filter(Order.order_purchase_timestamp <= clock, Order.order_purchase_timestamp >= cutoff)
        .group_by(OrderItem.product_id)
    )
    recent = {pid: int(n) for pid, n in recent_q.all()}

    clk = clock if clock.tzinfo else clock.replace(tzinfo=timezone.utc)

    out: Dict[str, Dict[str, float]] = {}
    for pid, total, first_dt, last_dt in q.all():
        if not first_dt:
            continue
        f_dt = first_dt if first_dt.tzinfo else first_dt.replace(tzinfo=timezone.utc)
        l_dt = last_dt if (last_dt and last_dt.tzinfo) else (last_dt.replace(tzinfo=timezone.utc) if last_dt else f_dt)
        # Calendar spans, not active-day spans: the recent rate divides by the
        # days the recent window actually covers for THIS product, and the
        # lifetime rate by the days since its first sale. Both rates now use
        # the same clock-based methodology, so comparing them (trend) and
        # blending them (stock model) is apples-to-apples. The previous mix —
        # a fixed 180-day divisor for recent vs an active-day span for
        # lifetime — understated the recent rate and marked nearly every
        # product DECREASING.
        span_days = max(1.0, (clk - f_dt).total_seconds() / 86400.0)
        recent_days = min(float(_RECENT_WINDOW_DAYS), span_days)
        units_recent = recent.get(pid, 0)
        days_active = max(1.0, (l_dt - f_dt).total_seconds() / 86400.0)
        out[pid] = {
            "units_total": int(total),
            "units_recent": units_recent,
            "units_recent_180d": units_recent,
            "daily_demand": round(units_recent / recent_days, 4),
            "lifetime_daily_demand": round(int(total) / span_days, 4),
            "days_active": round(days_active, 1),
        }
    # Keep only the newest clock (the clock advances as ingestion streams).
    _DEMAND_CACHE.clear()
    _DEMAND_CACHE[cache_key] = out
    if product_ids is None:
        return out
    wanted = {str(p) for p in product_ids}
    return {pid: stats for pid, stats in out.items() if pid in wanted}


def _turnover_band(daily_demand: float) -> str:
    if daily_demand >= 0.5:
        return "fast"
    if daily_demand >= 0.1:
        return "medium"
    return "slow"


def _modelled_stock(stats: Optional[Dict[str, float]]) -> Dict[str, Any]:
    """On-hand position modelled from real demand. Returns on_hand, ROP, cover days, band."""
    if not stats or stats.get("units_total", 0) == 0:
        return {"on_hand": 0, "daily_demand": 0.0, "reorder_point": 0, "days_of_cover": 0.0,
                "band": "none", "data_status": "NOT_ESTIMABLE"}
    life = stats["lifetime_daily_demand"] or 0.0
    recent = stats["daily_demand"] or 0.0
    # planning demand: recency-weighted blend of the recent-90d rate and the
    # lifetime rate (so a product that has sold 200 units but nothing lately is
    # still planned for, and a brand-new fast mover is caught quickly).
    daily = round(0.6 * recent + 0.4 * life, 4) if (recent or life) else 0.0
    if daily <= 0:
        daily = max(recent, life)
    band = _turnover_band(daily)
    cover_days = _COVER_DAYS_BY_TURNOVER[band]

    # trend factor: shrink the cover window when recent demand has fallen below the
    # lifetime rate (likely a stock cycle running down, or a stockout).
    trend = max(0.35, min(1.25, (recent / life))) if life > 0 else 0.7
    on_hand = int(round(daily * cover_days * trend))

    lead_demand = daily * _DEFAULT_LEAD_TIME
    safety = max(1, int(round(lead_demand * 0.4 + 1)))
    rop = max(safety + 1, int(round(lead_demand + safety)))
    doc = round(on_hand / daily, 1) if daily else 999.0
    return {
        "on_hand": on_hand,
        "daily_demand": round(recent, 3),
        "lifetime_daily_demand": round(life, 3),
        "planning_daily_demand": daily,
        "reorder_point": rop,
        "safety_stock": safety,
        "days_of_cover": doc,
        "band": band,
        "is_low": on_hand < rop,
        "trend_factor": round(trend, 2),
        "data_status": "MODELLED",
    }


def _deterministic_stock(product_id: str, default_threshold: int = 50) -> int:  # legacy shim
    return 0


class InventoryTools:
    """
    Stateless domain toolset for inventory monitoring, stock management,
    demand forecasting, and reorder planning.
    """

    @staticmethod
    def query_products(
        db: Session,
        threshold: Optional[int] = None,
        category: Optional[str] = None,
        limit: int = 50,
        low_stock_only: bool = False,
    ) -> List[InventoryProduct]:
        """
        Products with a MODELLED stock position derived from real sales demand.
        Only products with observed sales are returned (they are the ones we can model).
        "Low stock" = modelled on_hand < modelled reorder point (both data-derived).
        """
        # Rank by real demand so the view is the products that actually matter.
        demand = _demand_stats(db)
        if not demand:
            return []

        cat_en = {
            r[0]: r[1]
            for r in db.query(CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english).all()
        }

        ranked = sorted(demand.items(), key=lambda kv: (-kv[1]["units_recent_180d"], -kv[1]["units_total"]))

        # The low-stock test is a pure function of the (cached) demand stats, so
        # it is applied BEFORE touching the database — only the handful of
        # products actually returned get Product/price row lookups. This keeps
        # the SQL `IN` clauses tiny instead of scanning the whole catalogue.
        candidates: List[tuple] = []
        for pid, stats in ranked:
            pos = _modelled_stock(stats)
            is_low = pos["on_hand"] < pos["reorder_point"]
            if low_stock_only and not is_low:
                continue
            candidates.append((pid, stats, pos, is_low))
            if len(candidates) >= limit and not category:
                break
        if not candidates:
            return []

        cand_ids = [c[0] for c in candidates]
        prod_rows = {p.product_id: p for p in db.query(Product).filter(Product.product_id.in_(cand_ids)).all()}
        price_rows = dict(
            db.query(OrderItem.product_id, func.avg(OrderItem.price))
            .filter(OrderItem.product_id.in_(cand_ids))
            .group_by(OrderItem.product_id)
            .all()
        )

        out: List[InventoryProduct] = []
        for pid, stats, pos, is_low in candidates:
            p = prod_rows.get(pid)
            if category:
                cat_raw = (p.product_category_name if p else "") or ""
                if category.lower() not in cat_raw.lower() and category.lower() not in cat_en.get(cat_raw, "").lower():
                    continue
            cat_raw = (p.product_category_name if p else None) or "unknown"
            cat_disp = cat_en.get(cat_raw, cat_raw).replace("_", " ").title()
            weight = (p.product_weight_g if p else None) or None
            out.append(
                InventoryProduct(
                    id=pid, product_id=pid, sku=f"SKU-{pid[:8].upper()}",
                    name=f"{cat_disp} · {pid[:8]}", category=cat_disp,
                    stockQuantity=pos["on_hand"], stock_quantity=pos["on_hand"], available_stock=pos["on_hand"],
                    price=round(float(price_rows.get(pid) or 0.0), 2),
                    weight_g=weight,
                    reorder_required=is_low, reorder_flag=is_low,
                    lead_time_days=_DEFAULT_LEAD_TIME if (weight or 0) < 1000 else _DEFAULT_LEAD_TIME + 3,
                )
            )
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def get_product_by_id(db: Session, product_id: str) -> Optional[InventoryProduct]:
        """Look up a product by id/SKU/prefix or a category keyword (PT or EN)."""
        clean_id = str(product_id).strip()
        for junk in ("SKU-", "sku-", "PROD-", "prod-", "DC-", "dc-", "#"):
            clean_id = clean_id.replace(junk, "")
        clean_id = clean_id.strip()

        p = db.query(Product).filter(
            or_(Product.product_id == clean_id, Product.product_id.like(f"{clean_id}%"))
        ).first()
        if not p and clean_id:
            # keyword: match PT category or EN translation
            pt = [
                r[0] for r in db.query(CategoryTranslation.product_category_name)
                .filter(CategoryTranslation.product_category_name_english.ilike(f"%{clean_id}%")).all()
            ]
            cond = [Product.product_category_name.ilike(f"%{clean_id}%")]
            if pt:
                cond.append(Product.product_category_name.in_(pt))
            p = (
                db.query(Product)
                .join(OrderItem, OrderItem.product_id == Product.product_id)
                .filter(or_(*cond))
                .group_by(Product.product_id)
                .order_by(func.count(OrderItem.order_item_id).desc())
                .first()
            )
        if not p:
            return None

        stats = _demand_stats(db, [p.product_id]).get(p.product_id)
        pos = _modelled_stock(stats)
        avg_price = db.query(func.avg(OrderItem.price)).filter(OrderItem.product_id == p.product_id).scalar()
        cat_raw = p.product_category_name or "unknown"
        trans = db.query(CategoryTranslation.product_category_name_english).filter(
            CategoryTranslation.product_category_name == cat_raw
        ).scalar()
        cat_disp = (trans or cat_raw).replace("_", " ").title()
        weight = p.product_weight_g or None
        low = pos["on_hand"] < pos["reorder_point"]
        return InventoryProduct(
            id=p.product_id, product_id=p.product_id, sku=f"SKU-{p.product_id[:8].upper()}",
            name=f"{cat_disp} · {p.product_id[:8]}", category=cat_disp,
            stockQuantity=pos["on_hand"], stock_quantity=pos["on_hand"], available_stock=pos["on_hand"],
            price=round(float(avg_price or 0.0), 2), weight_g=weight,
            reorder_required=low, reorder_flag=low,
            lead_time_days=_DEFAULT_LEAD_TIME if (weight or 0) < 1000 else _DEFAULT_LEAD_TIME + 3,
        )

    @staticmethod
    def analyze_sales_trends(
        db: Session,
        product_id: Optional[str] = None,
        days: int = 30,
        limit: int = 20,
    ) -> List[SalesAnalysis]:
        """
        Calculates sales velocity, historical sales totals, daily averages,
        and demand trend (INCREASING, STABLE, DECREASING).
        """
        results: List[SalesAnalysis] = []

        ids = [product_id] if product_id else None
        demand = _demand_stats(db, [str(product_id).strip()]) if product_id else _demand_stats(db)
        if not demand:
            return []

        cat_en = {
            r[0]: r[1]
            for r in db.query(CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english).all()
        }
        pids = list(demand.keys())
        prod_rows = {p.product_id: p for p in db.query(Product).filter(Product.product_id.in_(pids)).all()}
        ranked = sorted(demand.items(), key=lambda kv: (-kv[1]["units_recent_180d"], -kv[1]["units_total"]))
        if not product_id:
            ranked = ranked[:limit]

        for pid, s in ranked:
            p = prod_rows.get(pid)
            cat_raw = (p.product_category_name if p else None) or "unknown"
            cat_disp = cat_en.get(cat_raw, cat_raw).replace("_", " ").title()
            daily = s["daily_demand"]
            life = s["lifetime_daily_demand"]
            # trend from recent vs lifetime daily demand — real signal
            if life > 0 and daily >= life * 1.25:
                trend = "INCREASING"
            elif life > 0 and daily <= life * 0.75:
                trend = "DECREASING"
            else:
                trend = "STABLE"
            lt = _DEFAULT_LEAD_TIME if ((p.product_weight_g if p else 0) or 0) < 1000 else _DEFAULT_LEAD_TIME + 3
            # Same reorder-quantity rule as get_reorder_recommendations
            # (cover lead time + a 30-day cycle, at least up to the ROP) so
            # this table and the Reorder Recommendations table agree.
            pos = _modelled_stock(s)
            reorder_qty = max(int(math.ceil(daily * (lt + 30))), pos["reorder_point"]) if daily > 0 else 1
            results.append(SalesAnalysis(
                product_id=pid, sku=f"SKU-{pid[:8].upper()}", name=f"{cat_disp} · {pid[:8]}",
                total_sales=s["units_total"],
                average_daily_sales=round(daily, 3),
                sales_velocity=round(life, 3),
                trend=trend,
                reorder_quantity=max(1, reorder_qty),
            ))
        return results

    @staticmethod
    def get_reorder_recommendations(
        db: Session,
        threshold: int = 50,
        limit: int = 15,
    ) -> List[ReorderRecommendation]:
        """
        Calculates optimal Reorder Point (ROP = d * L + SS) and suggested reorder quantities (EOQ-derived).
        Safety Stock is modelled as 40% of lead-time demand plus one unit — the documented
        parametric assumption on top of the measured daily demand (Olist has no warehouse feed).
        """
        low_stock_prods = InventoryTools.query_products(
            db, threshold=threshold, limit=limit, low_stock_only=True
        )
        pids = [p.product_id for p in low_stock_prods]
        stats_map = _demand_stats(db, pids)

        recommendations: List[ReorderRecommendation] = []
        for prod in low_stock_prods:
            lead_time = prod.lead_time_days or _DEFAULT_LEAD_TIME
            s = stats_map.get(prod.product_id, {})
            pos = _modelled_stock(s or None)
            daily_sales = pos["daily_demand"] or s.get("lifetime_daily_demand", 0.0)
            if daily_sales <= 0:
                continue

            lead_time_demand = daily_sales * lead_time
            safety_stock = pos.get("safety_stock") or max(1, int(math.ceil(lead_time_demand * 0.5)))
            rop = pos["reorder_point"] or int(math.ceil(lead_time_demand + safety_stock))
            suggested_qty = max(int(math.ceil(daily_sales * (lead_time + 30))), rop)  # cover lead time + a 30-day cycle
            # Cost basis is the observed selling price per unit (avg of real
            # order_items rows) — Est. Cost = suggested reorder qty x unit
            # price. The old hidden 0.62 "supplier discount" made the column
            # disagree with the unit price shown alongside it.
            unit_price = round(float(prod.price or 0.0), 2)
            est_cost = round(suggested_qty * unit_price, 2) if unit_price > 0 else None

            curr_stock = prod.stockQuantity or 0
            reason = (
                f"Modelled on-hand ({curr_stock}) < ROP ({rop}). "
                f"Demand {daily_sales}/day, {lead_time}d lead time; {s.get('units_total', 0)} units sold to date."
            )

            recommendations.append(
                ReorderRecommendation(
                    product_id=prod.product_id,
                    sku=prod.sku,
                    name=prod.name,
                    current_stock=curr_stock,
                    stock_quantity=curr_stock,
                    daily_sales=daily_sales,
                    average_daily_sales=daily_sales,
                    lead_time_days=lead_time,
                    safety_stock=safety_stock,
                    reorder_point=rop,
                    suggested_quantity=suggested_qty,
                    recommended_quantity=suggested_qty,
                    reorder_quantity=suggested_qty,
                    estimated_cost=est_cost,
                    supplier_unit_cost=unit_price,
                    reason=reason,
                )
            )

        return recommendations

    @staticmethod
    def get_inventory_alerts(
        db: Session,
        threshold: int = 50,
        limit: int = 10,
        recommendations: Optional[List[ReorderRecommendation]] = None,
    ) -> List[InventoryAlert]:
        """
        Generates alerts for low stock, impending stockouts, and high sales velocity.
        Pass an already-computed `recommendations` list to avoid recomputing it.
        """
        recs = (
            recommendations[:limit] if recommendations is not None
            else InventoryTools.get_reorder_recommendations(db, threshold=threshold, limit=limit)
        )
        alerts: List[InventoryAlert] = []
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for idx, r in enumerate(recs):
            stock = r.current_stock or 0
            rop = r.reorder_point or 1
            # Severity is relative to the SKU's own reorder point (its measured
            # lead-time demand), never absolute unit counts — 20 units is plenty
            # for a slow mover and critical for a fast one.
            if stock <= 0:
                severity = "CRITICAL"
                msg = f"Stockout: 0 units on hand while demand implies an ROP of {rop}. Immediate reorder advised."
            elif stock <= rop * 0.5:
                severity = "CRITICAL"
                msg = f"Critical stockout risk: only {stock} units remaining against an ROP of {rop}. Immediate reorder advised."
            elif stock <= rop:
                severity = "HIGH"
                msg = f"Low stock alert: {stock} units remaining (ROP: {rop}). Reorder suggested."
            else:
                severity = "MEDIUM"
                msg = f"Approaching reorder point: {stock} units remaining (ROP: {rop})."

            alerts.append(
                InventoryAlert(
                    id=f"alert-{r.product_id[:8]}-{idx}",
                    product_id=r.product_id,
                    sku=r.sku,
                    name=r.name,
                    severity=severity,
                    message=msg,
                    reason=r.reason,
                    created_at=now_str,
                    timestamp=now_str,
                )
            )

        return alerts

    @staticmethod
    def get_inventory_metrics(db: Session, threshold: int = 50) -> InventoryAgentMetrics:
        """
        High-level metrics, all from real data: catalog size, products with observed
        demand (the ones we can model), low-stock count, and a MODELLED inventory
        valuation = Σ(modelled on-hand × observed avg price) over demand-bearing products.
        """
        total_catalog = db.query(func.count(Product.product_id)).scalar() or 0
        demand = _demand_stats(db)
        active_pids = list(demand.keys())

        price_rows = dict(
            db.query(OrderItem.product_id, func.avg(OrderItem.price))
            .filter(OrderItem.product_id.in_(active_pids))
            .group_by(OrderItem.product_id)
            .all()
        ) if active_pids else {}

        low_stock_count = 0
        inv_value = 0.0
        for pid, s in demand.items():
            pos = _modelled_stock(s)
            if pos["is_low"]:
                low_stock_count += 1
            inv_value += pos["on_hand"] * float(price_rows.get(pid) or 0.0)

        return InventoryAgentMetrics(
            total_products=total_catalog or len(active_pids),
            low_stock_count=low_stock_count,
            reorder_count=low_stock_count,
            inventory_value=round(inv_value, 2),
        )

    @staticmethod
    def get_aggregates(
        db: Session,
        metric: str = "COUNT",
        group_by: Optional[str] = "category",
    ) -> Dict[str, Any]:
        """
        Calculates aggregate statistics across products or order items.
        """
        metric_upper = metric.upper()
        if group_by == "category":
            if metric_upper == "COUNT":
                rows = (
                    db.query(Product.product_category_name, func.count(Product.product_id))
                    .group_by(Product.product_category_name)
                    .order_by(desc(func.count(Product.product_id)))
                    .limit(10)
                    .all()
                )
                return {r[0] or "Unknown": r[1] for r in rows}
            elif metric_upper in ("AVG", "SUM"):
                rows = (
                    db.query(Product.product_category_name, func.avg(OrderItem.price))
                    .join(OrderItem, Product.product_id == OrderItem.product_id)
                    .group_by(Product.product_category_name)
                    .order_by(desc(func.avg(OrderItem.price)))
                    .limit(10)
                    .all()
                )
                return {r[0] or "Unknown": round(float(r[1] or 0.0), 2) for r in rows}

        return {"total_products": db.query(func.count(Product.product_id)).scalar() or 0}

    @staticmethod
    def execute_reorder(
        db: Session,
        product_id: str,
        quantity: int,
        supplier_notes: Optional[str] = None,
    ) -> InventoryAction:
        """
        Executes a formal reorder request, logging an action and updating inventory status.
        """
        prod = InventoryTools.get_product_by_id(db, product_id)
        prod_name = prod.name if prod else f"Product {product_id}"
        notes = f" ({supplier_notes})" if supplier_notes else ""

        action_msg = f"Purchase Order generated for {quantity} units of {prod_name}{notes}."
        logger.info(f"Inventory Watchdog Action: {action_msg}")

        return InventoryAction(
            action="CREATE_PURCHASE_ORDER",
            status="EXECUTED",
            product_id=product_id,
            message=action_msg,
        )


# ── LangChain ReAct Tools ─────────────────────────────────────────

@tool
def tool_query_inventory(threshold: int = 50, low_stock_only: bool = True) -> str:
    """Query inventory products and filter by low stock quantity threshold.
    Returns up to 15 rows plus the TOTAL number of matching products — always
    use the reported total (not the row count) when the question says "all"."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        prods = InventoryTools.query_products(db, threshold=threshold, limit=15, low_stock_only=low_stock_only)
        # Aggregate count across the whole catalogue (demand stats are cached,
        # so this is cheap) so answers over "all" products are not truncated.
        total = len(InventoryTools.query_products(db, threshold=threshold, limit=100000, low_stock_only=low_stock_only))
        lines = [f"Found {total} products matching threshold {threshold} (showing first {len(prods)}):"]
        for p in prods:
            lines.append(f"- {p.name} (ID: {p.product_id}): Stock={p.stockQuantity}, Price=R${p.price:.2f}, Reorder={p.reorder_required}")
        return "\n".join(lines)
    finally:
        db.close()


@tool
def tool_get_product_stock(product_id: str) -> str:
    """Look up complete inventory stock, price, category, and lead time for a specific product ID or keyword."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        p = InventoryTools.get_product_by_id(db, product_id)
        if not p:
            return f"Product '{product_id}' was not found in catalog."
        return (
            f"Product: {p.name}\n"
            f"ID: {p.product_id}\n"
            f"SKU: {p.sku}\n"
            f"Category: {p.category}\n"
            f"Current Stock: {p.stockQuantity}\n"
            f"Price: R${p.price:.2f}\n"
            f"Weight: {p.weight_g}g\n"
            f"Lead Time: {p.lead_time_days} days\n"
            f"Reorder Flag: {p.reorder_required}"
        )
    finally:
        db.close()


@tool
def tool_suggest_reorders(threshold: int = 50) -> str:
    """Calculate Reorder Point (ROP), Safety Stock, and suggested order quantities
    for low-stock items. The summary line reports the TOTAL item count and TOTAL
    estimated reorder cost across ALL low-stock products — use those aggregates
    (not the per-row sums) for "how many / total cost" questions."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        recs = InventoryTools.get_reorder_recommendations(db, threshold=threshold, limit=10)
        all_recs = InventoryTools.get_reorder_recommendations(db, threshold=threshold, limit=100000)
        lines = [
            f"Reorder summary: {len(all_recs)} low-stock products need restocking; "
            f"total estimated reorder cost R${sum(r.estimated_cost or 0 for r in all_recs):,.2f}. "
            f"Top {len(recs)} by urgency:"
        ]
        for r in recs:
            lines.append(
                f"- {r.name}: Stock={r.current_stock}, Daily Sales={r.daily_sales}/day, "
                f"ROP={r.reorder_point}, Suggested Order={r.suggested_quantity} units, Est Cost=R${r.estimated_cost:.2f}"
            )
        return "\n".join(lines)
    finally:
        db.close()


@tool
def tool_analyze_sales_trends(product_id: str = "", days: int = 30) -> str:
    """Analyze sales velocity, daily sales averages, and demand trajectory over a time period."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        trends = InventoryTools.analyze_sales_trends(db, product_id=product_id or None, days=days, limit=10)
        lines = [f"Sales Trends (last {days} days):"]
        for t in trends:
            lines.append(
                f"- {t.name}: Total Sales={t.total_sales}, Daily Avg={t.average_daily_sales}/day, "
                f"Trend={t.trend}, Reorder Qty={t.reorder_quantity}"
            )
        return "\n".join(lines)
    finally:
        db.close()


@tool
def tool_create_reorder_action(product_id: str, quantity: int, confirm: bool = False) -> str:
    """Create a Purchase Order to restock a product (SIDE-EFFECTING).

    Two-step protocol — never call with confirm=True on the first request:
      1. Preview the plan with `tool_get_product_stock` / `tool_suggest_reorders`,
         present stock, suggested quantity and estimated cost, and ask the user
         to reply with an explicit "confirm".
      2. Only after the user's latest message is that confirmation, pass
         confirm=True. The tool re-verifies the user's own words and refuses
         otherwise (the model cannot confirm on the user's behalf).
    """
    from app.agents.framework import user_explicitly_confirmed
    from app.database.session import SessionLocal

    if not confirm or not user_explicitly_confirmed():
        return (
            "CONFIRMATION_REQUIRED: no purchase order was created. Present this plan and ask the "
            f"user to reply 'confirm' to execute: product_id={product_id}, quantity={quantity}."
        )
    db = SessionLocal()
    try:
        act = InventoryTools.execute_reorder(db, product_id, quantity)
        return f"Reorder action executed: {act.message}"
    finally:
        db.close()


# ── Doc-grade demand analytics (velocity, concentration, restock priority) ──
@tool
def tool_demand_analytics(metric: str) -> str:
    """Historical demand analytics derived from real order-item data (Olist has
    NO real-time stock quantity — all restocking output is demand-based).

    metric must be one of:
    - "volume_concentration"   : avg units sold per product; products selling more than 2x the average
    - "top20_share"            : the 20 products with the largest share of all units sold, and their combined share
    - "monthly_velocity"       : 10 products with the highest average monthly sales velocity
    - "category_yoy"           : categories with the largest unit increase 2017 -> 2018 (with %)
    - "volatility"             : coefficient of variation of monthly sales for the top-selling products
    - "seasonal_concentration" : high-demand products whose sales concentrate in only a few months
    - "units_per_order_cat"    : average units sold per order for each category (highest first)
    - "seller_contribution"    : sellers with the highest total units sold and their % contribution
    - "velocity_growth"        : products whose sales velocity grew >= 50% between 2017 and 2018
    - "restock_priority"       : top 10 demand-based restocking priorities with the demand metric behind each
    """
    from collections import defaultdict

    from app.agents._shared import period_bucket, simulated_clock
    from app.database.session import SessionLocal
    from app.models.olist import CategoryTranslation, Order, OrderItem, Seller

    # Triage LLMs sometimes paraphrase the metric; normalize and alias.
    aliases = {
        "top20": "top20_share", "top_products": "top20_share", "top20products": "top20_share",
        "share": "top20_share", "concentration": "volume_concentration",
        "volatility": "volatility", "cv": "volatility",
        "restock": "restock_priority", "priority": "restock_priority",
        "restocking": "restock_priority", "restocking_priority": "restock_priority",
        "velocity": "monthly_velocity", "growth": "velocity_growth",
        "yoy": "category_yoy", "category_growth": "category_yoy",
        "seasonality": "seasonal_concentration", "units_per_order": "units_per_order_cat",
        "sellers": "seller_contribution",
    }
    norm = str(metric or "").strip().lower().replace(" ", "_").replace("-", "_")
    valid = {
        "volume_concentration", "top20_share", "monthly_velocity", "category_yoy", "volatility",
        "seasonal_concentration", "units_per_order_cat", "seller_contribution", "velocity_growth",
        "restock_priority",
    }
    if norm in valid:
        metric = norm
    elif norm in aliases:
        metric = aliases[norm]
    else:
        # Last resort: keyword scan of whatever text the triage passed through.
        q = norm
        if ("restock" in q or "priority" in q):
            metric = "restock_priority"
        elif ("volatil" in q or "coefficient" in q or " cv " in f" {q} "):
            metric = "volatility"
        elif ("20" in q or "share" in q or "percent" in q or "largest percentage" in q):
            metric = "top20_share"
        elif ("twice" in q or "2x" in q or "average" in q and "sold" in q):
            metric = "volume_concentration"
        elif ("2017" in q or "2018" in q or "yoy" in q):
            metric = "category_yoy"
        elif ("50" in q or "growth" in q):
            metric = "velocity_growth"
        elif ("per order" in q or "units_per_order" in q):
            metric = "units_per_order_cat"
        elif ("seller" in q):
            metric = "seller_contribution"
        elif ("velocity" in q or "monthly" in q):
            metric = "monthly_velocity"
        elif ("season" in q or "few months" in q or "concentrat" in q):
            metric = "seasonal_concentration"

    db = SessionLocal()
    try:
        clock = simulated_clock(db)
        month_bucket = period_bucket(db, Order.order_purchase_timestamp, "%Y-%m")
        rows = (
            db.query(
                OrderItem.product_id,
                month_bucket,
                func.count(OrderItem.order_item_id),
            )
            .join(Order, Order.order_id == OrderItem.order_id)
            .filter(Order.order_purchase_timestamp <= clock)
            .group_by(OrderItem.product_id, month_bucket)
            .all()
        )
        if not rows:
            return "No demand data observed yet."
        monthly: dict = defaultdict(lambda: defaultdict(int))
        totals: dict = {}
        for pid, ym, c in rows:
            monthly[pid][ym] += int(c)
            totals[pid] = totals.get(pid, 0) + int(c)
        cat_en = {
            r[0]: r[1]
            for r in db.query(CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english).all()
        }
        prod_cat = {
            r[0]: r[1]
            for r in db.query(Product.product_id, Product.product_category_name).all()
        }

        def disp(pid):
            raw = prod_cat.get(pid) or "unknown"
            return cat_en.get(raw, raw).replace("_", " ").title()

        avg_total = sum(totals.values()) / max(1, len(totals))

        if metric == "volume_concentration":
            hot = {pid: t for pid, t in totals.items() if t > 2 * avg_total}
            share = sum(hot.values()) / max(1, sum(totals.values())) * 100
            top = sorted(hot.items(), key=lambda kv: -kv[1])[:8]
            parts = ", ".join(f"{disp(p)[:24]} ({t:,} units)" for p, t in top)
            return (
                f"Average units sold per product: {avg_total:.1f}. {len(hot)} product(s) sell more than "
                f"2x that, representing {share:.1f}% of all units. Highest: {parts}"
            )
        if metric == "top20_share":
            total_units = sum(totals.values()) or 1
            top = sorted(totals.items(), key=lambda kv: -kv[1])[:20]
            share = sum(t for _, t in top) / total_units * 100
            parts = ", ".join(f"{disp(p)[:20]} {t / total_units * 100:.1f}%" for p, t in top[:8])
            return f"Top 20 products hold {share:.1f}% of all {total_units:,} units sold. Largest: {parts}"
        if metric == "monthly_velocity":
            vel = {pid: t / max(1, len(monthly[pid])) for pid, t in totals.items()}
            top = sorted(vel.items(), key=lambda kv: -kv[1])[:10]
            parts = ", ".join(f"{disp(p)[:20]}: {v:.1f}/mo" for p, v in top)
            return f"Top 10 products by average monthly velocity: {parts}"
        if metric == "category_yoy":
            per_year: dict = defaultdict(lambda: defaultdict(int))
            for pid, m in monthly.items():
                cat = disp(pid)
                for ym, c in m.items():
                    per_year[cat][ym[:4]] += c
            diffs = []
            for cat, y in per_year.items():
                a, b = y.get("2017", 0), y.get("2018", 0)
                if a > 0:
                    diffs.append((cat, a, b, (b - a) / a * 100))
            diffs.sort(key=lambda d: -d[3])
            parts = ", ".join(f"{c} {a:,}→{b:,} (+{g:.0f}%)" for c, a, b, g in diffs[:8])
            return "Largest unit-sold increase 2017→2018 by category: " + (parts or "no comparable data")
        if metric == "volatility":
            import statistics as st

            top = sorted(totals.items(), key=lambda kv: -kv[1])[:25]
            scored = []
            for pid, _ in top:
                vals = list(monthly[pid].values())
                if len(vals) >= 3 and st.mean(vals) > 0:
                    scored.append((pid, st.pstdev(vals) / st.mean(vals)))
            scored.sort(key=lambda kv: -kv[1])
            parts = ", ".join(f"{disp(p)[:20]} CV={cv:.2f}" for p, cv in scored[:8])
            return f"Demand volatility (CV of monthly sales, top sellers): {parts}. Higher CV = more volatile demand."
        if metric == "seasonal_concentration":
            out = []
            for pid, t in sorted(totals.items(), key=lambda kv: -kv[1])[:60]:
                m = monthly[pid]
                months_sorted = sorted(m.values(), reverse=True)
                cum, k = 0, 0
                for v in months_sorted:
                    cum += v
                    k += 1
                    if cum >= t * 0.8:
                        break
                if k <= 3 and t >= avg_total:
                    out.append((pid, t, k))
            parts = ", ".join(f"{disp(p)[:20]} (80% of {t:,} units in {k} month(s))" for p, t, k in out[:8])
            return "High-demand products concentrated in few months: " + (parts or "none in the current slice")
        if metric == "units_per_order_cat":
            items_per_order = (
                db.query(
                    Product.product_category_name,
                    func.count(OrderItem.order_item_id),
                    func.count(func.distinct(OrderItem.order_id)),
                )
                .join(Order, Order.order_id == OrderItem.order_id)
                .join(Product, Product.product_id == OrderItem.product_id)
                .filter(Order.order_purchase_timestamp <= clock)
                .group_by(Product.product_category_name)
                .all()
            )
            rows2 = [
                (cat_en.get(c, (c or "unknown")).replace("_", " ").title(), n / max(1, d))
                for c, n, d in items_per_order if c
            ]
            rows2.sort(key=lambda r: -r[1])
            parts = ", ".join(f"{c}: {v:.2f}" for c, v in rows2[:8])
            return f"Average units per order by category (highest first): {parts}"
        if metric == "seller_contribution":
            rows2 = (
                db.query(Seller.seller_id, func.count(OrderItem.order_item_id))
                .join(OrderItem, OrderItem.seller_id == Seller.seller_id)
                .join(Order, Order.order_id == OrderItem.order_id)
                .filter(Order.order_purchase_timestamp <= clock)
                .group_by(Seller.seller_id)
                .all()
            )
            total_units = sum(int(c) for _, c in rows2) or 1
            top = sorted(rows2, key=lambda r: -int(r[1]))[:8]
            parts = ", ".join(f"#{s[:8]} {int(c):,} ({int(c) / total_units * 100:.1f}%)" for s, c in top)
            return f"Sellers by total units sold: {parts}"
        if metric == "velocity_growth":
            out = []
            for pid, m in monthly.items():
                n17 = max(1, len([1 for ym in m if ym.startswith("2017")]))
                n18 = max(1, len([1 for ym in m if ym.startswith("2018")]))
                v17 = sum(c for ym, c in m.items() if ym.startswith("2017")) / n17
                v18 = sum(c for ym, c in m.items() if ym.startswith("2018")) / n18
                if v17 > 0 and v18 >= v17 * 1.5:
                    out.append((pid, v17, v18, (v18 - v17) / v17 * 100))
            out.sort(key=lambda r: -r[3])
            parts = ", ".join(f"{disp(p)[:18]} {a:.1f}→{b:.1f}/mo (+{g:.0f}%)" for p, a, b, g in out[:8])
            return f"Products with ≥50% velocity growth 2017→2018: {len(out)} found. Highest: " + (parts or "none")
        if metric == "restock_priority":
            scored = []
            for pid, t in totals.items():
                m = monthly[pid]
                recent = sum(c for ym, c in m.items() if ym in sorted(m)[-3:])
                rec_vel = recent / max(1, min(3, len(m)))
                vel = t / max(1, len(m))
                score = rec_vel * 0.7 + vel * 0.3
                scored.append((pid, score, rec_vel, vel, t))
            scored.sort(key=lambda r: -r[1])
            parts = "; ".join(
                f"{disp(p)[:18]} (score {s:.1f} = 0.7*recent {rv:.1f}/mo + 0.3*overall {v:.1f}/mo; {t:,} units)"
                for p, s, rv, v, t in scored[:10]
            )
            return (
                "Demand-based restocking priority (NOT a stockout prediction — Olist provides no current "
                "inventory quantities): " + parts
            )
        return (
            "Unknown metric. Use one of: volume_concentration, top20_share, monthly_velocity, category_yoy, "
            "volatility, seasonal_concentration, units_per_order_cat, seller_contribution, velocity_growth, restock_priority"
        )
    except Exception as exc:  # noqa: BLE001
        return f"❌ Analytics failed: {exc}"
    finally:
        db.close()


ALL_INVENTORY_TOOLS = [
    tool_query_inventory,
    tool_get_product_stock,
    tool_suggest_reorders,
    tool_analyze_sales_trends,
    tool_create_reorder_action,
    tool_demand_analytics,
]
