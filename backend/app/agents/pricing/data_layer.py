"""
Pricing data layer — margin distribution, discount leakage, and category price
benchmarks from DataCo (profit per order) + Olist (item prices), observed up to
the simulated clock.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents._shared import pct, simulated_clock
from app.intelligence.statistics.profiler import StatisticalProfiler
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.models.olist import OrderItem, Product


class PricingData:
    @staticmethod
    def margin_overview(db: Session) -> dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(DataCoOrder.order_total, DataCoOrder.order_profit)
            .filter(DataCoOrder.order_date <= clock, DataCoOrder.order_total > 0)
            # Most-recent-first: an unordered LIMIT would otherwise silently
            # sample whatever the database's on-disk order happens to be —
            # after a chronological CSV import, that's the OLDEST orders.
            .order_by(DataCoOrder.order_date.desc())
            .limit(8000)
            .all()
        )
        if not rows:
            return {"status": "NOT_ESTIMABLE", "sample_count": 0}
        margins = [pct(float(p or 0), float(t)) for t, p in rows if t]
        revenue = sum(float(t) for t, _ in rows)
        profit = sum(float(p or 0) for _, p in rows)
        prof = StatisticalProfiler.profile(margins)
        loss_making = sum(1 for m in margins if m < 0)
        return {
            "status": "OK",
            "sample_count": len(rows),
            "total_revenue": round(revenue, 2),
            "total_profit": round(profit, 2),
            "blended_margin_pct": pct(profit, revenue),
            "median_order_margin_pct": prof.median,
            "p25_margin_pct": prof.q25,
            "loss_making_orders": loss_making,
            "loss_making_rate_pct": pct(loss_making, len(margins)),
            "lower_fence_pct": round(prof.lower_outer_fence, 2),
        }

    @staticmethod
    def margin_by_segment(db: Session, limit: int = 12) -> dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(
                DataCoOrder.market,
                func.count(DataCoOrder.order_id),
                func.sum(DataCoOrder.order_total),
                func.sum(DataCoOrder.order_profit),
            )
            .filter(DataCoOrder.order_date <= clock, DataCoOrder.order_total > 0)
            .group_by(DataCoOrder.market)
            .order_by(func.sum(DataCoOrder.order_total).desc())
            .limit(limit)
            .all()
        )
        return {
            "segments": [
                {
                    "segment": m or "Unknown",
                    "orders": int(n),
                    "revenue": round(float(rev or 0), 2),
                    "profit": round(float(prof or 0), 2),
                    "margin_pct": pct(float(prof or 0), float(rev or 0)),
                }
                for m, n, rev, prof in rows
            ]
        }

    @staticmethod
    def discount_leakage(db: Session, limit: int = 12) -> dict[str, Any]:
        clock = simulated_clock(db)
        # DataCoOrderItem may or may not be populated depending on ingestion path.
        rows = (
            db.query(
                DataCoOrderItem.category_name,
                func.count(DataCoOrderItem.order_item_id),
                func.avg(DataCoOrderItem.order_item_discount_rate),
                func.avg(DataCoOrderItem.order_item_profit_ratio),
                func.sum(DataCoOrderItem.order_item_discount),
            )
            .join(DataCoOrder, DataCoOrder.order_id == DataCoOrderItem.order_id)
            .filter(DataCoOrder.order_date <= clock)
            .group_by(DataCoOrderItem.category_name)
            .order_by(func.sum(DataCoOrderItem.order_item_discount).desc())
            .limit(limit)
            .all()
        )
        cats = []
        for cat, n, disc_rate, profit_ratio, disc_total in rows:
            cats.append(
                {
                    "category": cat or "Unknown",
                    "lines": int(n),
                    "avg_discount_rate_pct": round(float(disc_rate or 0) * 100, 2),
                    "avg_profit_ratio_pct": round(float(profit_ratio or 0) * 100, 2),
                    "total_discount_given": round(float(disc_total or 0), 2),
                }
            )
        return {"categories": cats, "available": bool(cats)}

    @staticmethod
    def order_margin_lookup(db: Session, order_id: str) -> dict[str, Any]:
        """Resolves margin/price/freight for ONE specific order id — DataCo (has profit) first, then Olist item prices."""
        clean = str(order_id or "").strip().replace("#", "")
        if not clean:
            return {"status": "NOT_FOUND", "reason": "No order id provided."}

        try:
            dc = db.query(DataCoOrder).filter(DataCoOrder.order_id == int(clean)).first()
        except ValueError:
            dc = None
        if dc is not None:
            total = float(dc.order_total or 0.0)
            profit = float(dc.order_profit or 0.0)
            return {
                "status": "OK",
                "order_id": dc.order_id,
                "source": "DataCo",
                "revenue": round(total, 2),
                "profit": round(profit, 2),
                "margin_pct": pct(profit, total),
                "market": dc.market,
                "region": dc.order_region,
            }

        items = db.query(OrderItem).filter(OrderItem.order_id.ilike(f"{clean}%")).all()
        if items:
            price = sum(float(i.price or 0) for i in items)
            freight = sum(float(i.freight_value or 0) for i in items)
            return {
                "status": "OK",
                "order_id": clean,
                "source": "Olist",
                "item_price_total": round(price, 2),
                "freight_total": round(freight, 2),
                "freight_pct_of_price": pct(freight, price),
                "note": "Olist has no per-order profit figure; margin is not computable for this order, only price/freight.",
            }

        return {"status": "NOT_FOUND", "reason": f"No order/pricing record found for #{clean}."}

    @staticmethod
    def olist_category_prices(db: Session, limit: int = 15) -> dict[str, Any]:
        rows = (
            db.query(
                Product.product_category_name,
                func.count(OrderItem.order_item_id),
                func.avg(OrderItem.price),
                func.avg(OrderItem.freight_value),
            )
            .join(OrderItem, OrderItem.product_id == Product.product_id)
            .group_by(Product.product_category_name)
            .order_by(func.count(OrderItem.order_item_id).desc())
            .limit(limit)
            .all()
        )
        return {
            "categories": [
                {
                    "category": (c or "unknown").replace("_", " ").title(),
                    "units": int(n),
                    "avg_price": round(float(p or 0), 2),
                    "avg_freight": round(float(f or 0), 2),
                    "freight_pct_of_price": pct(float(f or 0), float(p or 0)),
                }
                for c, n, p, f in rows
            ]
        }
