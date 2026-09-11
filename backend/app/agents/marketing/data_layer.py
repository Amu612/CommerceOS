"""
Marketing data layer — RFM customer segmentation, repeat-purchase rate, category
demand trend, and customer-segment mix, from Olist + DataCo up to the simulated clock.
"""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents._shared import pct, simulated_clock, tz
from app.models.dataco import DataCoOrder
from app.models.olist import Customer, Order, OrderItem, Product


class MarketingData:
    @staticmethod
    def rfm(db: Session) -> Dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(
                Customer.customer_unique_id,
                func.count(func.distinct(Order.order_id)),
                func.max(Order.order_purchase_timestamp),
                func.coalesce(func.sum(OrderItem.price), 0.0),
            )
            .join(Order, Order.customer_id == Customer.customer_id)
            .outerjoin(OrderItem, OrderItem.order_id == Order.order_id)
            .filter(Order.order_purchase_timestamp <= clock)
            .group_by(Customer.customer_unique_id)
            .all()
        )
        if not rows:
            return {"status": "NOT_ESTIMABLE", "sample_count": 0}

        customers = []
        for uid, freq, last_dt, monetary in rows:
            last = tz(last_dt)
            recency_days = round((clock - last).total_seconds() / 86400.0, 1) if last else None
            customers.append({"uid": uid, "frequency": int(freq), "recency_days": recency_days, "monetary": float(monetary or 0)})

        total = len(customers)
        repeat = sum(1 for c in customers if c["frequency"] >= 2)
        monetary_vals = sorted(c["monetary"] for c in customers)
        freq_vals = sorted(c["frequency"] for c in customers)
        rec_vals = sorted(c["recency_days"] for c in customers if c["recency_days"] is not None)

        def q(vals, p):
            return vals[min(len(vals) - 1, int(len(vals) * p))] if vals else 0

        m_hi, r_recent = q(monetary_vals, 0.8), q(rec_vals, 0.33)
        segments = {"Champions": 0, "Loyal": 0, "Promising": 0, "At Risk": 0, "Hibernating": 0, "New": 0}
        for c in customers:
            r, f, mv = c["recency_days"], c["frequency"], c["monetary"]
            if r is None:
                segments["New"] += 1
            elif f >= 2 and mv >= m_hi and r <= r_recent:
                segments["Champions"] += 1
            elif f >= 2 and r <= r_recent:
                segments["Loyal"] += 1
            elif f == 1 and r <= r_recent:
                segments["Promising"] += 1
            elif r > q(rec_vals, 0.66):
                segments["Hibernating"] += 1
            else:
                segments["At Risk"] += 1

        return {
            "status": "OK",
            "sample_count": total,
            "total_customers": total,
            "repeat_customers": repeat,
            "repeat_rate_pct": pct(repeat, total),
            "avg_order_value": round(sum(monetary_vals) / max(1, total), 2),
            "segments": [{"segment": k, "customers": v, "share_pct": pct(v, total)} for k, v in segments.items()],
            "top_monetary_threshold": round(m_hi, 2),
        }

    @staticmethod
    def category_demand_trend(db: Session, limit: int = 10) -> Dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(
                Product.product_category_name,
                func.count(OrderItem.order_item_id),
                func.coalesce(func.sum(OrderItem.price), 0.0),
            )
            .join(OrderItem, OrderItem.product_id == Product.product_id)
            .join(Order, Order.order_id == OrderItem.order_id)
            .filter(Order.order_purchase_timestamp <= clock)
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
                    "revenue": round(float(rev or 0), 2),
                }
                for c, n, rev in rows
            ]
        }

    @staticmethod
    def segment_mix(db: Session) -> Dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(DataCoOrder.customer_segment, func.count(DataCoOrder.order_id), func.sum(DataCoOrder.order_total))
            .filter(DataCoOrder.order_date <= clock)
            .group_by(DataCoOrder.customer_segment)
            .order_by(func.count(DataCoOrder.order_id).desc())
            .all()
        )
        total = sum(int(n) for _, n, _ in rows) or 1
        return {
            "segments": [
                {"segment": s or "Unknown", "orders": int(n), "share_pct": pct(int(n), total),
                 "revenue": round(float(rev or 0), 2)}
                for s, n, rev in rows
            ]
        }
