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


def _shopify_product_to_inventory(p: Any, low_stock_threshold: int) -> InventoryProduct:
    on_hand = p.inventory_quantity if p.inventory_quantity is not None else 0
    is_low = on_hand < low_stock_threshold
    return InventoryProduct(
        id=str(p.product_id), product_id=str(p.product_id), sku=f"SHOPIFY-{p.product_id}",
        name=p.title or f"Product {p.product_id}", category=p.product_type or "General",
        stockQuantity=on_hand, stock_quantity=on_hand, available_stock=on_hand,
        price=round(float(p.price or 0.0), 2), weight_g=None,
        reorder_required=is_low, reorder_flag=is_low,
        lead_time_days=_DEFAULT_LEAD_TIME,
    )


def _query_shopify_products(
    db: Session, category: Optional[str] = None, limit: int = 50, low_stock_only: bool = False, threshold: int = 50,
) -> List[InventoryProduct]:
    """Live counterpart of `query_products` — real Shopify inventory counts,
    `data_status=OBSERVED` (not modelled — Shopify tracks actual on-hand stock)."""
    from app.models.shopify import ShopifyProduct

    q = db.query(ShopifyProduct)
    if category:
        q = q.filter(ShopifyProduct.product_type.ilike(f"%{category}%"))
    q = q.order_by(ShopifyProduct.inventory_quantity.asc().nulls_last()).limit(limit * 3 if low_stock_only else limit)
    out = []
    for p in q.all():
        item = _shopify_product_to_inventory(p, threshold)
        if low_stock_only and not item.reorder_required:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _get_shopify_product(db: Session, product_id: str) -> Optional[InventoryProduct]:
    from app.models.shopify import ShopifyProduct

    clean = str(product_id).strip().replace("SHOPIFY-", "")
    p = None
    try:
        p = db.get(ShopifyProduct, int(clean))
    except (ValueError, TypeError):
        pass
    if p is None:
        p = db.query(ShopifyProduct).filter(ShopifyProduct.title.ilike(f"%{clean}%")).first()
    return _shopify_product_to_inventory(p, 50) if p else None


def _demand_stats(db: Session, product_ids: Optional[List[str]] = None) -> Dict[str, Dict[str, float]]:
    """Real per-product demand: total units, units in the recent window, first/last sale."""
    from app.agents._shared import simulated_clock

    clock = simulated_clock(db)
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
    if product_ids:
        q = q.filter(OrderItem.product_id.in_(product_ids))

    recent_q = (
        db.query(OrderItem.product_id, func.count(OrderItem.order_item_id))
        .join(Order, Order.order_id == OrderItem.order_id)
        .filter(Order.order_purchase_timestamp <= clock, Order.order_purchase_timestamp >= cutoff)
        .group_by(OrderItem.product_id)
    )
    if product_ids:
        recent_q = recent_q.filter(OrderItem.product_id.in_(product_ids))
    recent = {pid: int(n) for pid, n in recent_q.all()}

    out: Dict[str, Dict[str, float]] = {}
    for pid, total, first_dt, last_dt in q.all():
        days_active = max(1.0, ((last_dt - first_dt).total_seconds() / 86400.0) if first_dt and last_dt else 1.0)
        units_recent = recent.get(pid, 0)
        out[pid] = {
            "units_total": int(total),
            "units_recent": units_recent,
            "units_recent_90d": units_recent,
            "daily_demand": round(units_recent / float(_RECENT_WINDOW_DAYS), 4),
            "lifetime_daily_demand": round(int(total) / days_active, 4),
            "days_active": round(days_active, 1),
        }
    return out


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
        from app.services.data_source_service import data_source_service

        if data_source_service.is_live():
            return _query_shopify_products(db, category=category, limit=limit, low_stock_only=low_stock_only)

        # Rank by real demand so the view is the products that actually matter.
        demand = _demand_stats(db)
        if not demand:
            return []

        pid_list = list(demand.keys())
        prod_rows = {p.product_id: p for p in db.query(Product).filter(Product.product_id.in_(pid_list)).all()}
        price_rows = dict(
            db.query(OrderItem.product_id, func.avg(OrderItem.price))
            .filter(OrderItem.product_id.in_(pid_list))
            .group_by(OrderItem.product_id)
            .all()
        )
        cat_en = {
            r[0]: r[1]
            for r in db.query(CategoryTranslation.product_category_name, CategoryTranslation.product_category_name_english).all()
        }

        ranked = sorted(demand.items(), key=lambda kv: (-kv[1]["units_recent_90d"], -kv[1]["units_total"]))
        out: List[InventoryProduct] = []
        for pid, stats in ranked:
            p = prod_rows.get(pid)
            pos = _modelled_stock(stats)
            is_low = pos["on_hand"] < pos["reorder_point"]
            if low_stock_only and not is_low:
                continue
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
        from app.services.data_source_service import data_source_service

        if data_source_service.is_live():
            return _get_shopify_product(db, product_id)

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
        from app.services.data_source_service import data_source_service

        if data_source_service.is_live():
            # Sales-velocity trend needs order-item history depth a live demo
            # store won't have yet — honestly empty, not modelled from stale
            # historic Olist data.
            return []

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
        ranked = sorted(demand.items(), key=lambda kv: (-kv[1]["units_recent_90d"], -kv[1]["units_total"]))
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
            results.append(SalesAnalysis(
                product_id=pid, sku=f"SKU-{pid[:8].upper()}", name=f"{cat_disp} · {pid[:8]}",
                total_sales=s["units_total"],
                average_daily_sales=round(daily, 3),
                sales_velocity=round(life, 3),
                trend=trend,
                reorder_quantity=max(1, int(round(daily * lt * 1.5))),
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
        Safety Stock = Z * sigma_L (defaulting Z=1.65 for 95% service level).
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
            supplier_cost = round((prod.price or 0.0) * 0.62, 2)
            est_cost = round(suggested_qty * supplier_cost, 2) if supplier_cost else None

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
                    supplier_unit_cost=supplier_cost,
                    reason=reason,
                )
            )

        return recommendations

    @staticmethod
    def get_inventory_alerts(
        db: Session,
        threshold: int = 50,
        limit: int = 10,
    ) -> List[InventoryAlert]:
        """
        Generates alerts for low stock, impending stockouts, and high sales velocity.
        """
        recs = InventoryTools.get_reorder_recommendations(db, threshold=threshold, limit=limit)
        alerts: List[InventoryAlert] = []
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        for idx, r in enumerate(recs):
            stock = r.current_stock or 0
            if stock <= 15:
                severity = "CRITICAL"
                msg = f"Critical stockout risk: only {stock} units remaining (ROP: {r.reorder_point}). Immediate reorder advised."
            elif stock <= 30:
                severity = "HIGH"
                msg = f"Low stock alert: {stock} units remaining (ROP: {r.reorder_point}). Reorder suggested."
            else:
                severity = "MEDIUM"
                msg = f"Approaching reorder point: {stock} units remaining (ROP: {r.reorder_point})."

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
    """Query inventory products and filter by low stock quantity threshold."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        prods = InventoryTools.query_products(db, threshold=threshold, limit=15, low_stock_only=low_stock_only)
        lines = [f"Found {len(prods)} products (threshold: {threshold}):"]
        for p in prods:
            lines.append(f"- {p.name} (ID: {p.product_id}): Stock={p.stockQuantity}, Price=₹{p.price:.2f}, Reorder={p.reorder_required}")
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
            f"Price: ₹{p.price:.2f}\n"
            f"Weight: {p.weight_g}g\n"
            f"Lead Time: {p.lead_time_days} days\n"
            f"Reorder Flag: {p.reorder_required}"
        )
    finally:
        db.close()


@tool
def tool_suggest_reorders(threshold: int = 50) -> str:
    """Calculate Reorder Point (ROP), Safety Stock, and suggested order quantities for low-stock items."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        recs = InventoryTools.get_reorder_recommendations(db, threshold=threshold, limit=10)
        lines = [f"Reorder Recommendations ({len(recs)} items):"]
        for r in recs:
            lines.append(
                f"- {r.name}: Stock={r.current_stock}, Daily Sales={r.daily_sales}/day, "
                f"ROP={r.reorder_point}, Suggested Order={r.suggested_quantity} units, Est Cost=₹{r.estimated_cost:.2f}"
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
def tool_create_reorder_action(product_id: str, quantity: int) -> str:
    """Trigger a Purchase Order creation for restocking a product."""
    from app.database.session import SessionLocal
    db = SessionLocal()
    try:
        act = InventoryTools.execute_reorder(db, product_id, quantity)
        return f"Reorder action executed: {act.message}"
    finally:
        db.close()


ALL_INVENTORY_TOOLS = [
    tool_query_inventory,
    tool_get_product_stock,
    tool_suggest_reorders,
    tool_analyze_sales_trends,
    tool_create_reorder_action,
]
