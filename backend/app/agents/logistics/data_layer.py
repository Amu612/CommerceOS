"""
Logistics data layer — carrier/lane performance, transit-time distributions, and
SLA/late-delivery risk, computed from Olist + DataCo records observed up to the
simulated clock. Zero hardcoded thresholds; severities come from empirical fences.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents._shared import pct, simulated_clock, tz
from app.intelligence.statistics.profiler import StatisticalProfiler
from app.models.dataco import DataCoOrder
from app.models.olist import Order

_OLIST_DELIVERED = "delivered"


class LogisticsData:
    @staticmethod
    def carrier_performance(db: Session, limit: int = 12) -> Dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(
                DataCoOrder.shipping_mode,
                func.count(DataCoOrder.order_id),
                func.avg(DataCoOrder.days_for_shipping_real),
                func.avg(DataCoOrder.days_for_shipment_scheduled),
                func.sum(DataCoOrder.late_delivery_risk),
            )
            .filter(DataCoOrder.order_date <= clock, DataCoOrder.days_for_shipping_real.isnot(None))
            .group_by(DataCoOrder.shipping_mode)
            .order_by(func.count(DataCoOrder.order_id).desc())
            .limit(limit)
            .all()
        )
        carriers: List[Dict[str, Any]] = []
        for mode, n, real, sched, late in rows:
            real = float(real or 0.0)
            sched = float(sched or 0.0)
            carriers.append(
                {
                    "carrier": mode or "Unknown",
                    "shipments": int(n),
                    "avg_transit_days": round(real, 2),
                    "avg_scheduled_days": round(sched, 2),
                    "avg_delay_days": round(real - sched, 2),
                    "late_risk_rate_pct": pct(int(late or 0), int(n)),
                }
            )
        return {"carriers": carriers, "sample_count": sum(c["shipments"] for c in carriers)}

    @staticmethod
    def lane_performance(db: Session, limit: int = 12) -> Dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(
                DataCoOrder.order_region,
                func.count(DataCoOrder.order_id),
                func.avg(DataCoOrder.days_for_shipping_real),
                func.avg(DataCoOrder.days_for_shipment_scheduled),
                func.sum(DataCoOrder.late_delivery_risk),
            )
            .filter(DataCoOrder.order_date <= clock, DataCoOrder.days_for_shipping_real.isnot(None))
            .group_by(DataCoOrder.order_region)
            .order_by(func.count(DataCoOrder.order_id).desc())
            .limit(limit)
            .all()
        )
        lanes = []
        for region, n, real, sched, late in rows:
            real, sched = float(real or 0), float(sched or 0)
            lanes.append(
                {
                    "lane": region or "Unknown",
                    "shipments": int(n),
                    "avg_transit_days": round(real, 2),
                    "sla_gap_days": round(real - sched, 2),
                    "late_risk_rate_pct": pct(int(late or 0), int(n)),
                }
            )
        return {"lanes": lanes}

    @staticmethod
    def transit_distribution(db: Session, sample: int = 5000) -> Dict[str, Any]:
        clock = simulated_clock(db)
        vals: List[float] = [
            float(v)
            for (v,) in db.query(DataCoOrder.days_for_shipping_real)
            .filter(DataCoOrder.order_date <= clock, DataCoOrder.days_for_shipping_real.isnot(None))
            .limit(sample)
            .all()
            if v is not None and float(v) >= 0
        ]
        if not vals:
            return {"status": "NOT_ESTIMABLE", "sample_count": 0}
        p = StatisticalProfiler.profile(vals)
        return {
            "status": "OK",
            "sample_count": len(vals),
            "median_days": p.median,
            "p90_days": round(sorted(vals)[int(len(vals) * 0.9)], 2),
            "max_days": p.max_val,
            "mean_days": p.mean,
            "outlier_fence_days": round(p.upper_outer_fence, 2),
            "outliers": sum(1 for v in vals if v > p.upper_outer_fence),
        }

    @staticmethod
    def olist_delivery_sla(db: Session, sample: int = 6000) -> Dict[str, Any]:
        clock = simulated_clock(db)
        rows = (
            db.query(Order.order_delivered_customer_date, Order.order_estimated_delivery_date)
            .filter(
                Order.order_purchase_timestamp <= clock,
                Order.order_status == _OLIST_DELIVERED,
                Order.order_delivered_customer_date.isnot(None),
                Order.order_estimated_delivery_date.isnot(None),
            )
            .limit(sample)
            .all()
        )
        margins = [
            (tz(d) - tz(e)).total_seconds() / 86400.0 for d, e in rows if d and e
        ]
        if not margins:
            return {"status": "NOT_ESTIMABLE", "sample_count": 0}
        late = sum(1 for m in margins if m > 0)
        p = StatisticalProfiler.profile(margins)
        if p.median > 0:
            sla = "DEGRADED"
        elif p.q75 > 0:
            sla = "AT_RISK"
        else:
            sla = "OPTIMAL"
        return {
            "status": "OK",
            "sample_count": len(margins),
            "on_time_rate_pct": pct(len(margins) - late, len(margins)),
            "late_deliveries": late,
            "median_margin_days": round(p.median, 2),
            "p90_margin_days": round(sorted(margins)[int(len(margins) * 0.9)], 2),
            "sla_health": sla,
        }

    @staticmethod
    def shipment_lookup(db: Session, order_id: str) -> Dict[str, Any]:
        """Resolves a single order/shipment id against DataCo (shipping-mode + transit data) then Olist."""
        clean = str(order_id or "").strip().replace("#", "")
        if not clean:
            return {"status": "NOT_FOUND", "reason": "No order id provided."}

        try:
            dc = db.query(DataCoOrder).filter(DataCoOrder.order_id == int(clean)).first()
        except ValueError:
            dc = None
        if dc is not None:
            real = float(dc.days_for_shipping_real) if dc.days_for_shipping_real is not None else None
            sched = float(dc.days_for_shipment_scheduled) if dc.days_for_shipment_scheduled is not None else None
            return {
                "status": "OK",
                "order_id": dc.order_id,
                "source": "DataCo",
                "carrier": dc.shipping_mode or "Unknown",
                "region": dc.order_region or "Unknown",
                "scheduled_days": sched,
                "actual_days": real,
                "delay_days": round(real - sched, 2) if real is not None and sched is not None else None,
                "late_delivery_risk": bool(dc.late_delivery_risk),
                "order_status": dc.order_status,
            }

        order = db.query(Order).filter(Order.order_id.ilike(f"{clean}%")).first()
        if order is not None:
            delivered = tz(order.order_delivered_customer_date)
            estimated = tz(order.order_estimated_delivery_date)
            margin_days = round((delivered - estimated).total_seconds() / 86400.0, 2) if delivered and estimated else None
            return {
                "status": "OK",
                "order_id": order.order_id,
                "source": "Olist",
                "order_status": order.order_status,
                "estimated_delivery_date": estimated.isoformat()[:10] if estimated else None,
                "delivered_date": delivered.isoformat()[:10] if delivered else None,
                "delivery_margin_days": margin_days,
                "late": bool(margin_days and margin_days > 0),
            }

        return {"status": "NOT_FOUND", "reason": f"No shipment found for order #{clean}."}

    @staticmethod
    def late_risk_overview(db: Session) -> Dict[str, Any]:
        clock = simulated_clock(db)
        total = db.query(func.count(DataCoOrder.order_id)).filter(DataCoOrder.order_date <= clock).scalar() or 0
        at_risk = (
            db.query(func.count(DataCoOrder.order_id))
            .filter(DataCoOrder.order_date <= clock, DataCoOrder.late_delivery_risk == 1)
            .scalar()
            or 0
        )
        return {"total_shipments": int(total), "at_risk": int(at_risk), "at_risk_rate_pct": pct(at_risk, total)}
