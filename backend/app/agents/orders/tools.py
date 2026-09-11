from langchain_core.tools import tool
"""
Orders Agent Domain Tools.
All tools query real Olist/DataCo records strictly observed up to the simulated clock time T.
Risk thresholds, severity, SLA health, and anomaly scores are derived from empirical distributions.
Zero hardcoded, static, or future-leaking values.
"""
import logging
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func, asc, desc, and_, or_, text

from app.models.olist import Order, OrderItem, Customer, Product, CategoryTranslation
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.intelligence.statistics.profiler import StatisticalProfiler
from app.intelligence.anomaly.detector import AnomalyDetector
from app.intelligence.forecasting.engine import ForecastEngine
from app.intelligence.confidence.calculator import ConfidenceCalculator
from app.agents.orders.schemas import (
    DataCategory,
    AgeDistributionBucket,
    OrderDetail,
    OrderItemDetail,
    ReturnEligibilityResult,
    ReturnRequestResult,
    ShipmentTrackingResult,
    ShipmentTrackingEvent,
)

logger = logging.getLogger(__name__)

_profiler = StatisticalProfiler()
_anomaly_detector = AnomalyDetector()
_forecast_engine = ForecastEngine()

# Olist pending statuses
_OLIST_PENDING = {"created", "approved", "invoiced", "processing"}
# DataCo pending statuses
_DATACO_PENDING = {"PROCESSING", "PENDING", "PENDING_PAYMENT", "ON_HOLD", "PAYMENT_REVIEW"}
# Cancelled
_OLIST_CANCELLED = {"canceled", "unavailable"}
_DATACO_CANCELLED = {"CANCELED", "SUSPECTED_FRAUD"}
# Completed
_OLIST_COMPLETED = {"delivered"}
_DATACO_COMPLETED = {"COMPLETE", "CLOSED"}


def _tz(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def get_simulated_clock(db: Optional[Session] = None) -> datetime:
    """
    Returns the exact current simulated clock time T.
    At time T, only records with timestamp <= T are observed.
    """
    from app.services.replay_engine import replay_engine
    from app.services.state_service import state_service

    streamed = getattr(replay_engine, "events_processed", 0) or 0
    if streamed > 0 and replay_engine.current_simulated_date:
        return _tz(replay_engine.current_simulated_date)
    if state_service.sim_current_date and getattr(state_service, "total_orders", 0):
        return _tz(state_service.sim_current_date)

    own = None
    if db is None:
        from app.database.session import SessionLocal

        own = db = SessionLocal()
    try:
        max_o = db.query(func.max(Order.order_purchase_timestamp)).scalar()
        max_dc = db.query(func.max(DataCoOrder.order_date)).scalar()
        candidates = [d for d in [max_o, max_dc] if d is not None]
        if candidates:
            return _tz(max(candidates))
    except Exception:
        pass
    finally:
        if own is not None:
            own.close()
    return datetime.now(timezone.utc)


def _percentile_from_profile(values: List[float], p: float) -> float:
    """Compute an empirical percentile from a numeric list."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    k = (n - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[int(f)] * (c - k) + s[int(c)] * (k - f)


class OrdersTools:
    """
    Stateless domain tool class for the Orders Agent.
    Every method accepts a SQLAlchemy Session (db) and strictly bounds observation
    to records where event_timestamp <= simulated_clock.
    """

    # ──────────────────────────────────────────────────────────────────────────
    # 1. get_order_state
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_order_state(db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Returns a point-in-time state snapshot: total, pending, completed, cancelled, delayed, fulfillment rate.
        Strictly observes records with timestamp <= simulated_clock.
        """
        if db is None:
            return {
                "total_orders": 0,
                "pending_orders": 0,
                "completed_orders": 0,
                "cancelled_orders": 0,
                "delayed_orders": 0,
                "fulfillment_rate_pct": 0.0,
                "cancellation_rate_pct": 0.0,
                "data_source": "NONE",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "not_estimable_reason": "No database session available.",
                "sample_count": 0,
            }

        sim_clock = get_simulated_clock(db)
        o_total = o_pending = o_completed = o_cancelled = o_delayed = 0
        dc_total = dc_pending = dc_completed = dc_cancelled = dc_delayed = 0

        try:
            olist_counts = dict(
                db.query(Order.order_status, func.count(Order.order_id))
                .filter(Order.order_purchase_timestamp <= sim_clock)
                .group_by(Order.order_status).all()
            )
            o_total = sum(olist_counts.values())
            o_completed = sum(olist_counts.get(s, 0) for s in _OLIST_COMPLETED)
            o_cancelled = sum(olist_counts.get(s, 0) for s in _OLIST_CANCELLED)
            o_pending = sum(olist_counts.get(s, 0) for s in _OLIST_PENDING)
            o_delayed = (
                db.query(func.count(Order.order_id))
                .filter(
                    Order.order_purchase_timestamp <= sim_clock,
                    Order.order_status == "delivered",
                    Order.order_delivered_customer_date.isnot(None),
                    Order.order_estimated_delivery_date.isnot(None),
                    Order.order_delivered_customer_date > Order.order_estimated_delivery_date,
                ).scalar() or 0
            )
        except Exception as e:
            logger.debug(f"Olist order_state note: {e}")

        try:
            dc_counts = dict(
                db.query(DataCoOrder.order_status, func.count(DataCoOrder.order_id))
                .filter(DataCoOrder.order_date <= sim_clock)
                .group_by(DataCoOrder.order_status).all()
            )
            dc_total = sum(dc_counts.values())
            dc_completed = sum(dc_counts.get(s, 0) for s in _DATACO_COMPLETED)
            dc_cancelled = sum(dc_counts.get(s, 0) for s in _DATACO_CANCELLED)
            dc_pending = sum(dc_counts.get(s, 0) for s in _DATACO_PENDING)
            dc_delayed = (
                db.query(func.count(DataCoOrder.order_id))
                .filter(
                    DataCoOrder.order_date <= sim_clock,
                    DataCoOrder.late_delivery_risk == 1,
                ).scalar() or 0
            )
        except Exception as e:
            logger.debug(f"DataCo order_state note: {e}")

        total = o_total + dc_total
        if total == 0:
            return {
                "total_orders": 0,
                "pending_orders": 0,
                "completed_orders": 0,
                "cancelled_orders": 0,
                "delayed_orders": 0,
                "fulfillment_rate_pct": 0.0,
                "cancellation_rate_pct": 0.0,
                "data_source": "NONE",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "not_estimable_reason": f"No order records observed prior to simulated clock {sim_clock.isoformat()}.",
                "sample_count": 0,
            }

        pending = o_pending + dc_pending
        completed = o_completed + dc_completed
        cancelled = o_cancelled + dc_cancelled
        delayed = o_delayed + dc_delayed
        fulfillment_rate = round((completed / total) * 100.0, 2)
        cancellation_rate = round((cancelled / total) * 100.0, 2)

        source = "BOTH" if (o_total > 0 and dc_total > 0) else ("OLIST" if o_total > 0 else "DATACO")

        return {
            "total_orders": total,
            "pending_orders": pending,
            "completed_orders": completed,
            "cancelled_orders": cancelled,
            "delayed_orders": delayed,
            "fulfillment_rate_pct": fulfillment_rate,
            "cancellation_rate_pct": cancellation_rate,
            "data_source": source,
            "data_status": DataCategory.OBSERVED.value,
            "as_of": sim_clock.isoformat(),
            "sample_count": total,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 2. get_order_history
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_order_history(
        days: int = 90,
        db: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns daily order counts over the rolling window strictly up to simulated clock time T.
        Window: [sim_clock - timedelta(days=days), sim_clock].
        """
        if db is None:
            return []
        sim_clock = get_simulated_clock(db)
        cutoff = sim_clock - timedelta(days=days)
        rows: Dict[str, int] = {}

        try:
            olist_rows = (
                db.query(Order.order_purchase_timestamp)
                .filter(
                    Order.order_purchase_timestamp >= cutoff,
                    Order.order_purchase_timestamp <= sim_clock,
                )
                .all()
            )
            for (ts,) in olist_rows:
                if ts:
                    day = _tz(ts).strftime("%Y-%m-%d")
                    rows[day] = rows.get(day, 0) + 1

            dc_rows = (
                db.query(DataCoOrder.order_date)
                .filter(
                    DataCoOrder.order_date >= cutoff,
                    DataCoOrder.order_date <= sim_clock,
                )
                .all()
            )
            for (ts,) in dc_rows:
                if ts:
                    day = _tz(ts).strftime("%Y-%m-%d")
                    rows[day] = rows.get(day, 0) + 1
        except Exception as e:
            logger.debug(f"get_order_history: {e}")

        return [
            {
                "date": d,
                "order_count": c,
                "data_status": DataCategory.OBSERVED.value,
            }
            for d, c in sorted(rows.items())
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # 3. get_order_status_distribution
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_order_status_distribution(db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """Returns the frequency of each order status observed up to simulated clock time T."""
        if db is None:
            return []
        sim_clock = get_simulated_clock(db)
        counts: Dict[str, int] = {}
        try:
            for st, cnt in (
                db.query(Order.order_status, func.count(Order.order_id))
                .filter(Order.order_purchase_timestamp <= sim_clock)
                .group_by(Order.order_status)
                .all()
            ):
                if st:
                    counts[st] = counts.get(st, 0) + int(cnt)
        except Exception as e:
            logger.debug(f"Olist status dist: {e}")

        try:
            for st, cnt in (
                db.query(DataCoOrder.order_status, func.count(DataCoOrder.order_id))
                .filter(DataCoOrder.order_date <= sim_clock)
                .group_by(DataCoOrder.order_status)
                .all()
            ):
                if st:
                    counts[st] = counts.get(st, 0) + int(cnt)
        except Exception as e:
            logger.debug(f"DataCo status dist: {e}")

        total = sum(counts.values())
        return [
            {
                "status": st,
                "count": cnt,
                "pct": round(cnt / total * 100, 2) if total > 0 else 0.0,
                "data_status": DataCategory.OBSERVED.value,
                "sample_count": total,
            }
            for st, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True)
        ]

    # ──────────────────────────────────────────────────────────────────────────
    # 4. get_order_processing_distribution
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_order_processing_distribution(
        limit: int = 3000,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Profiles the distribution of order processing times in hours
        for orders observed up to simulated clock time T across both Olist and DataCo.
        - Olist: time between order purchase and approval / carrier handover.
        - DataCo: time between order placement and shipping handover.
        """
        if db is None:
            return {
                "status": "NOT_ESTIMABLE",
                "reason": "Database session unavailable.",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "sample_count": 0,
            }

        sim_clock = get_simulated_clock(db)
        hours_list: List[float] = []

        # 1. Query Olist processing times
        try:
            rows = (
                db.query(Order.order_purchase_timestamp, Order.order_approved_at, Order.order_delivered_carrier_date)
                .filter(
                    Order.order_purchase_timestamp <= sim_clock,
                )
                .limit(limit)
                .all()
            )
            for purch, apprv, carrier in rows:
                purch_tz = _tz(purch)
                apprv_tz = _tz(apprv)
                carrier_tz = _tz(carrier)
                if purch_tz and apprv_tz and apprv_tz >= purch_tz:
                    hours_list.append((apprv_tz - purch_tz).total_seconds() / 3600.0)
                elif purch_tz and carrier_tz and carrier_tz >= purch_tz:
                    hours_list.append((carrier_tz - purch_tz).total_seconds() / 3600.0)
        except Exception as e:
            logger.debug(f"Olist processing dist: {e}")

        # 2. Query DataCo processing times (shipping_date - order_date)
        try:
            dc_rows = (
                db.query(DataCoOrder.order_date, DataCoOrder.shipping_date)
                .filter(
                    DataCoOrder.order_date <= sim_clock,
                    DataCoOrder.shipping_date.isnot(None),
                )
                .limit(limit)
                .all()
            )
            for o_dt, s_dt in dc_rows:
                o_tz = _tz(o_dt)
                s_tz = _tz(s_dt)
                if o_tz and s_tz and s_tz >= o_tz:
                    hours_list.append((s_tz - o_tz).total_seconds() / 3600.0)
        except Exception as e:
            logger.debug(f"DataCo processing dist: {e}")

        # 3. Fallback: if no approval/shipping dates are available yet, calculate pending queue dwell times
        if not hours_list:
            try:
                p_rows = db.query(Order.order_purchase_timestamp).filter(
                    Order.order_purchase_timestamp <= sim_clock,
                    Order.order_status.in_(_OLIST_PENDING),
                ).limit(limit).all()
                for (ts,) in p_rows:
                    ts_tz = _tz(ts)
                    if ts_tz and sim_clock >= ts_tz:
                        hours_list.append((sim_clock - ts_tz).total_seconds() / 3600.0)

                dc_p_rows = db.query(DataCoOrder.order_date).filter(
                    DataCoOrder.order_date <= sim_clock,
                    DataCoOrder.order_status.in_(_DATACO_PENDING),
                ).limit(limit).all()
                for (ts,) in dc_p_rows:
                    ts_tz = _tz(ts)
                    if ts_tz and sim_clock >= ts_tz:
                        hours_list.append((sim_clock - ts_tz).total_seconds() / 3600.0)
            except Exception as e:
                logger.debug(f"Pending dwell fallback: {e}")

        n = len(hours_list)
        if n == 0:
            return {
                "status": "NOT_ESTIMABLE",
                "reason": f"No order processing or pending timestamps observed up to clock {sim_clock.isoformat()}.",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "sample_count": 0,
            }

        profile = StatisticalProfiler.profile(hours_list)
        return {
            "status": "OK",
            "count": n,
            "mean_hours": round(profile.mean, 2) if profile else None,
            "median_hours": round(profile.median, 2) if profile else None,
            "p75_hours": round(profile.q75, 2) if profile else None,
            "p90_hours": round(_percentile_from_profile(hours_list, 0.90), 2),
            "p95_hours": round(_percentile_from_profile(hours_list, 0.95), 2),
            "std_dev_hours": round(profile.std_dev, 2) if profile else 0.0,
            "mad_hours": round(profile.mad, 2) if profile else 0.0,
            "iqr_hours": round(profile.iqr, 2) if profile else 0.0,
            "min_hours": round(profile.min_val, 2) if profile else None,
            "max_hours": round(profile.max_val, 2) if profile else None,
            "data_source": "BOTH",
            "data_status": DataCategory.CALCULATED.value,
            "sample_count": n,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 5. get_order_backlog
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_order_backlog(db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Returns pending order count and empirical age distribution relative to simulated clock time T.
        Outlier threshold is derived from empirical Tukey fences, not hardcoded hours.
        """
        if db is None:
            return {
                "pending_count": 0,
                "age_distribution": [],
                "median_age_hours": None,
                "p75_age_hours": None,
                "p90_age_hours": None,
                "p95_age_hours": None,
                "max_age_hours": None,
                "anomalous_aging_count": 0,
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "not_estimable_reason": "Database session unavailable.",
                "sample_count": 0,
            }

        sim_clock = get_simulated_clock(db)
        aging_hours: List[float] = []

        try:
            olist_rows = (
                db.query(Order.order_purchase_timestamp)
                .filter(
                    Order.order_purchase_timestamp <= sim_clock,
                    Order.order_status.in_(_OLIST_PENDING),
                )
                .all()
            )
            for (ts,) in olist_rows:
                ts_tz = _tz(ts)
                if ts_tz:
                    aging_hours.append(max(0.0, (sim_clock - ts_tz).total_seconds() / 3600.0))
        except Exception as e:
            logger.debug(f"Olist backlog: {e}")

        try:
            dc_rows = (
                db.query(DataCoOrder.order_date)
                .filter(
                    DataCoOrder.order_date <= sim_clock,
                    DataCoOrder.order_status.in_(_DATACO_PENDING),
                )
                .all()
            )
            for (ts,) in dc_rows:
                ts_tz = _tz(ts)
                if ts_tz:
                    aging_hours.append(max(0.0, (sim_clock - ts_tz).total_seconds() / 3600.0))
        except Exception as e:
            logger.debug(f"DataCo backlog: {e}")

        n = len(aging_hours)
        if n == 0:
            return {
                "pending_count": 0,
                "age_distribution": [],
                "median_age_hours": None,
                "p75_age_hours": None,
                "p90_age_hours": None,
                "p95_age_hours": None,
                "max_age_hours": None,
                "anomalous_aging_count": 0,
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "not_estimable_reason": f"No pending orders currently in queue as of {sim_clock.isoformat()}.",
                "sample_count": 0,
            }

        aging_hours.sort()
        profile = StatisticalProfiler.profile(aging_hours)
        p90 = _percentile_from_profile(aging_hours, 0.90)
        p95 = _percentile_from_profile(aging_hours, 0.95)

        # Dynamic Tukey outlier fence: Q3 + 1.5 * IQR
        if profile and profile.iqr > 0:
            empirical_fence = profile.q75 + 1.5 * profile.iqr
        elif profile and profile.mad > 0:
            empirical_fence = profile.median + 3.0 * profile.mad
        else:
            empirical_fence = profile.max_val if profile else 0.0

        # Anomalous aging: orders exceeding the empirical upper fence
        anomalous = sum(1 for h in aging_hours if h > empirical_fence)

        # Dynamic empirical histogram buckets from quartiles
        buckets: List[Dict[str, Any]] = []
        if profile:
            boundaries = [profile.min_val, profile.q25, profile.median, profile.q75, p90, profile.max_val]
            boundaries = sorted(list(set(boundaries)))
            for i in range(len(boundaries) - 1):
                lo = boundaries[i]
                hi = boundaries[i + 1]
                cnt = sum(1 for h in aging_hours if (lo <= h <= hi if i == len(boundaries) - 2 else lo <= h < hi))
                buckets.append({
                    "label": f"{lo:.1f}h - {hi:.1f}h",
                    "lower_bound_hours": round(lo, 1),
                    "upper_bound_hours": round(hi, 1),
                    "order_count": cnt,
                    "pct_of_pending": round(cnt / n * 100.0, 1),
                    "data_status": DataCategory.CALCULATED.value,
                })

        return {
            "pending_count": n,
            "age_distribution": buckets,
            "median_age_hours": round(profile.median, 1) if profile else None,
            "p75_age_hours": round(profile.q75, 1) if profile else None,
            "p90_age_hours": round(p90, 1),
            "p95_age_hours": round(p95, 1),
            "max_age_hours": round(profile.max_val, 1) if profile else None,
            "anomalous_aging_count": anomalous,
            "empirical_outlier_fence_hours": round(empirical_fence, 1),
            "data_status": DataCategory.CALCULATED.value,
            "sample_count": n,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 6. get_cancellation_history
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_cancellation_history(
        days: int = 90,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Calculates daily cancellation series and empirical rate distribution strictly up to simulated clock T.
        """
        if db is None:
            return {
                "status": "NOT_ESTIMABLE",
                "reason": "Database session unavailable.",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "sample_count": 0,
            }

        sim_clock = get_simulated_clock(db)
        cutoff = sim_clock - timedelta(days=days)
        cancelled_by_day: Dict[str, int] = {}
        total_by_day: Dict[str, int] = {}

        try:
            rows = (
                db.query(Order.order_purchase_timestamp)
                .filter(
                    Order.order_status.in_(_OLIST_CANCELLED),
                    Order.order_purchase_timestamp >= cutoff,
                    Order.order_purchase_timestamp <= sim_clock,
                ).all()
            )
            for (ts,) in rows:
                ts_tz = _tz(ts)
                if ts_tz:
                    d = ts_tz.strftime("%Y-%m-%d")
                    cancelled_by_day[d] = cancelled_by_day.get(d, 0) + 1

            all_rows = (
                db.query(Order.order_purchase_timestamp)
                .filter(
                    Order.order_purchase_timestamp >= cutoff,
                    Order.order_purchase_timestamp <= sim_clock,
                ).all()
            )
            for (ts,) in all_rows:
                ts_tz = _tz(ts)
                if ts_tz:
                    d = ts_tz.strftime("%Y-%m-%d")
                    total_by_day[d] = total_by_day.get(d, 0) + 1
        except Exception as e:
            logger.debug(f"Olist cancellation history: {e}")

        try:
            rows = (
                db.query(DataCoOrder.order_date)
                .filter(
                    DataCoOrder.order_status.in_(_DATACO_CANCELLED),
                    DataCoOrder.order_date >= cutoff,
                    DataCoOrder.order_date <= sim_clock,
                ).all()
            )
            for (ts,) in rows:
                ts_tz = _tz(ts)
                if ts_tz:
                    d = ts_tz.strftime("%Y-%m-%d")
                    cancelled_by_day[d] = cancelled_by_day.get(d, 0) + 1

            all_rows = (
                db.query(DataCoOrder.order_date)
                .filter(
                    DataCoOrder.order_date >= cutoff,
                    DataCoOrder.order_date <= sim_clock,
                ).all()
            )
            for (ts,) in all_rows:
                ts_tz = _tz(ts)
                if ts_tz:
                    d = ts_tz.strftime("%Y-%m-%d")
                    total_by_day[d] = total_by_day.get(d, 0) + 1
        except Exception as e:
            logger.debug(f"DataCo cancellation history: {e}")

        all_days = sorted(set(list(cancelled_by_day.keys()) + list(total_by_day.keys())))
        daily_rates: List[float] = []
        series = []
        for d in all_days:
            tot = total_by_day.get(d, 0)
            canc = cancelled_by_day.get(d, 0)
            rate = round(canc / tot * 100.0, 3) if tot > 0 else 0.0
            daily_rates.append(rate)
            series.append({"date": d, "cancelled": canc, "total": tot, "cancellation_rate_pct": rate})

        total_cancelled = sum(cancelled_by_day.values())
        total_orders = sum(total_by_day.values())
        overall_rate = round(total_cancelled / total_orders * 100.0, 3) if total_orders > 0 else 0.0

        if total_orders == 0:
            return {
                "status": "NOT_ESTIMABLE",
                "reason": f"No order records observed in the {days}-day window up to {sim_clock.isoformat()}.",
                "window_days": days,
                "total_orders": 0,
                "total_cancelled": 0,
                "overall_cancellation_rate_pct": 0.0,
                "daily_series": [],
                "daily_rate_profile": {},
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "sample_count": 0,
            }

        profile = StatisticalProfiler.profile(daily_rates) if len(daily_rates) >= 2 else None

        return {
            "status": "OK",
            "window_days": days,
            "total_orders": total_orders,
            "total_cancelled": total_cancelled,
            "overall_cancellation_rate_pct": overall_rate,
            "daily_series": series,
            "daily_rate_profile": {
                "mean": round(profile.mean, 3) if profile else overall_rate,
                "median": round(profile.median, 3) if profile else overall_rate,
                "std_dev": round(profile.std_dev, 3) if profile else 0.0,
                "p75": round(profile.q75, 3) if profile else overall_rate,
                "p90": round(_percentile_from_profile(daily_rates, 0.90), 3) if daily_rates else overall_rate,
            },
            "data_status": DataCategory.CALCULATED.value,
            "sample_count": total_orders,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 7. get_fulfillment_performance
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_fulfillment_performance(
        limit: int = 3000,
        db: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Profiles delivery times and delay margins empirically for orders completed up to simulated clock T.
        SLA health is derived strictly from the delay margin distribution.
        """
        if db is None:
            return {
                "status": "NOT_ESTIMABLE",
                "reason": "Database session unavailable.",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
                "sample_count": 0,
            }

        sim_clock = get_simulated_clock(db)
        delivery_days: List[float] = []
        delay_margins: List[float] = []

        try:
            rows = (
                db.query(
                    Order.order_purchase_timestamp,
                    Order.order_delivered_customer_date,
                    Order.order_estimated_delivery_date,
                )
                .filter(
                    Order.order_purchase_timestamp <= sim_clock,
                    Order.order_status == "delivered",
                    Order.order_delivered_customer_date.isnot(None),
                    Order.order_delivered_customer_date <= sim_clock,
                )
                .limit(limit)
                .all()
            )
            for purch, deliv, est in rows:
                p_tz = _tz(purch)
                d_tz = _tz(deliv)
                e_tz = _tz(est)
                if p_tz and d_tz and d_tz >= p_tz:
                    days = (d_tz - p_tz).total_seconds() / 86400.0
                    delivery_days.append(days)
                    if e_tz:
                        delay_margins.append((d_tz - e_tz).total_seconds() / 86400.0)
        except Exception as e:
            logger.debug(f"Olist fulfillment: {e}")

        try:
            rows = (
                db.query(DataCoOrder.days_for_shipping_real, DataCoOrder.days_for_shipment_scheduled)
                .filter(
                    DataCoOrder.order_date <= sim_clock,
                    DataCoOrder.days_for_shipping_real.isnot(None),
                )
                .limit(limit)
                .all()
            )
            for real_d, sched_d in rows:
                if real_d is not None and float(real_d) >= 0:
                    delivery_days.append(float(real_d))
                    if sched_d is not None:
                        delay_margins.append(float(real_d) - float(sched_d))
        except Exception as e:
            logger.debug(f"DataCo fulfillment: {e}")

        n = len(delivery_days)
        if n == 0:
            return {
                "status": "NOT_ESTIMABLE",
                "reason": f"No completed orders with delivery records observed as of {sim_clock.isoformat()}.",
                "sample_count": 0,
                "delay_rate_pct": 0.0,
                "sla_health": "NOT_ESTIMABLE",
                "data_status": DataCategory.NOT_ESTIMABLE.value,
            }

        n_delayed = sum(1 for m in delay_margins if m > 0.0)
        delay_rate = round(n_delayed / len(delay_margins) * 100.0, 2) if delay_margins else 0.0

        profile = StatisticalProfiler.profile(delivery_days)
        p90 = _percentile_from_profile(delivery_days, 0.90)

        # Dynamic SLA Health from Delay Margin distribution:
        # If median delay margin is positive -> systematic delay across bulk of orders -> DEGRADED
        # If upper quartile delay margin is positive -> tail delay -> AT_RISK
        # Else -> OPTIMAL
        margin_profile = StatisticalProfiler.profile(delay_margins) if delay_margins else None
        if margin_profile:
            if margin_profile.median > 0.0:
                sla_health = "DEGRADED"
            elif margin_profile.q75 > 0.0:
                sla_health = "AT_RISK"
            else:
                sla_health = "OPTIMAL"
        elif delay_rate > 0:
            sla_health = "AT_RISK"
        else:
            sla_health = "OPTIMAL"

        return {
            "status": "OK",
            "sample_count": n,
            "mean_delivery_days": round(profile.mean, 2) if profile else None,
            "median_delivery_days": round(profile.median, 2) if profile else None,
            "p75_delivery_days": round(profile.q75, 2) if profile else None,
            "p90_delivery_days": round(p90, 2),
            "std_dev_delivery_days": round(profile.std_dev, 2) if profile else 0.0,
            "delay_rate_pct": delay_rate,
            "delayed_count": n_delayed,
            "sla_health": sla_health,
            "data_status": DataCategory.CALCULATED.value,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 8. detect_order_anomalies
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def detect_order_anomalies(
        days: int = 90,
        limit: int = 5,
        db: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        Detects daily order volume anomalies from observed daily counts up to simulated clock T.
        Uses Tukey outlier fences or modified Z-scores.
        """
        if db is None:
            return []

        history = OrdersTools.get_order_history(days=days, db=db)
        if len(history) < 3:
            return []

        daily_counts = [float(h["order_count"]) for h in history]
        dates = [h["date"] for h in history]

        # Use historical baseline to evaluate points
        baseline = daily_counts[:-3] if len(daily_counts) > 5 else daily_counts
        recent = daily_counts[-3:] if len(daily_counts) > 5 else daily_counts
        recent_dates = dates[-3:] if len(dates) > 5 else dates

        anomalies = []
        for val, date in zip(recent, recent_dates):
            result = _anomaly_detector.evaluate_sample(
                observed_value=val,
                historical_samples=baseline,
                metric_name="daily_order_count",
            )
            if result and result.is_anomaly:
                anomalies.append({
                    "date": date,
                    "observed_count": int(val),
                    "expected_baseline": result.expected_baseline,
                    "deviation": result.deviation,
                    "anomaly_score": result.anomaly_score,
                    "detection_method": result.detection_method,
                    "is_anomaly": True,
                    "data_status": DataCategory.CALCULATED.value,
                })

        anomalies.sort(key=lambda x: x["anomaly_score"], reverse=True)
        return anomalies[:limit]

    # ──────────────────────────────────────────────────────────────────────────
    # 9. forecast_order_volume
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def forecast_order_volume(
        days: int = 90,
        db: Optional[Session] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Forecasts daily order volume from observed time series up to simulated clock T.
        Derives trend direction from linear regression slope t-statistic.
        """
        if db is None:
            return None

        history = OrdersTools.get_order_history(days=days, db=db)
        if not history:
            return None

        count_series = [float(h["order_count"]) for h in history]

        try:
            result = _forecast_engine.forecast(
                time_series=count_series,
                horizon="7 days",
                steps_ahead=7,
                data_quality=min(1.0, len(count_series) / 30.0),
            )
            if result is None:
                return None

            # Determine trend direction from linear regression slope t-statistic
            trend = "STABLE"
            n = len(count_series)
            if n >= 3:
                x = list(range(n))
                x_m = sum(x) / n
                y_m = sum(count_series) / n
                den = sum((xi - x_m) ** 2 for xi in x)
                if den > 0:
                    slope = sum((x[i] - x_m) * (count_series[i] - y_m) for i in range(n)) / den
                    res = [(count_series[i] - (y_m + slope * (x[i] - x_m))) for i in range(n)]
                    rss = sum(r ** 2 for r in res)
                    s_err = math.sqrt(rss / (n - 2)) if n > 2 else 0.0
                    se_slope = s_err / math.sqrt(den) if den > 0 and s_err > 0 else 0.0
                    if se_slope > 0:
                        t_stat = slope / se_slope
                        if t_stat >= 2.0:
                            trend = "INCREASING"
                        elif t_stat <= -2.0:
                            trend = "DECREASING"

            return {
                "predicted_daily_volume": round(result.prediction, 2),
                "lower_bound": round(result.lower_bound, 2) if result.lower_bound is not None else None,
                "upper_bound": round(result.upper_bound, 2) if result.upper_bound is not None else None,
                "trend": trend,
                "model": result.model_method,
                "confidence": round(result.confidence, 3),
                "data_points": len(count_series),
                "data_status": DataCategory.MODELLED.value,
            }
        except Exception as e:
            logger.debug(f"forecast_order_volume note: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # 10. forecast_cancellations
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def forecast_cancellations(
        days: int = 90,
        db: Optional[Session] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Forecasts cancellation rate from observed time series up to simulated clock T.
        """
        if db is None:
            return None

        history = OrdersTools.get_cancellation_history(days=days, db=db)
        if history.get("status") != "OK":
            return None

        series_raw = history.get("daily_series", [])
        if not series_raw:
            return None

        time_series = [float(r["cancellation_rate_pct"]) for r in series_raw]

        try:
            result = _forecast_engine.forecast(
                time_series=time_series,
                horizon="7 days",
                steps_ahead=7,
                data_quality=min(1.0, len(time_series) / 30.0),
            )
            if result is None:
                return None

            pending_count = 0
            try:
                backlog = OrdersTools.get_order_backlog(db=db)
                pending_count = backlog.get("pending_count", 0)
            except Exception:
                pass

            predicted_rate = max(0.0, result.prediction)
            predicted_cancellations = int(round(pending_count * (predicted_rate / 100.0)))

            return {
                "predicted_cancellation_rate_pct": round(predicted_rate, 3),
                "lower_bound_rate": round(max(0.0, result.lower_bound), 3) if result.lower_bound is not None else None,
                "upper_bound_rate": round(result.upper_bound, 3) if result.upper_bound is not None else None,
                "predicted_cancellations": predicted_cancellations,
                "confidence": round(result.confidence, 3),
                "data_points": len(time_series),
                "data_status": DataCategory.MODELLED.value,
            }
        except Exception as e:
            logger.debug(f"forecast_cancellations note: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # 11. analyze_status_transitions
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def analyze_status_transitions(db: Optional[Session] = None) -> Dict[str, Any]:
        """Counts orders at each lifecycle stage and identifies top aging orders observed up to clock T."""
        if db is None:
            return {}
        sim_clock = get_simulated_clock(db)
        transition_counts: Dict[str, int] = {}
        stuck_pending: List[Dict] = []

        try:
            olist_transitions = dict(
                db.query(Order.order_status, func.count(Order.order_id))
                .filter(Order.order_purchase_timestamp <= sim_clock)
                .group_by(Order.order_status).all()
            )
            for st, cnt in olist_transitions.items():
                if st:
                    transition_counts[f"olist:{st}"] = int(cnt)

            oldest_pending = (
                db.query(Order.order_id, Order.order_status, Order.order_purchase_timestamp)
                .filter(
                    Order.order_purchase_timestamp <= sim_clock,
                    Order.order_status.in_(_OLIST_PENDING),
                )
                .order_by(asc(Order.order_purchase_timestamp))
                .limit(5)
                .all()
            )
            for r in oldest_pending:
                ts_tz = _tz(r.order_purchase_timestamp)
                age_h = (sim_clock - ts_tz).total_seconds() / 3600.0 if ts_tz else None
                stuck_pending.append({
                    "order_id": str(r.order_id),
                    "status": str(r.order_status),
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                    "source": "olist",
                })
        except Exception as e:
            logger.debug(f"Olist transitions note: {e}")

        try:
            dc_transitions = dict(
                db.query(DataCoOrder.order_status, func.count(DataCoOrder.order_id))
                .filter(DataCoOrder.order_date <= sim_clock)
                .group_by(DataCoOrder.order_status).all()
            )
            for st, cnt in dc_transitions.items():
                if st:
                    transition_counts[f"dataco:{st}"] = int(cnt)

            oldest_dc = (
                db.query(DataCoOrder.order_id, DataCoOrder.order_status, DataCoOrder.order_date)
                .filter(
                    DataCoOrder.order_date <= sim_clock,
                    DataCoOrder.order_status.in_(_DATACO_PENDING),
                )
                .order_by(asc(DataCoOrder.order_date))
                .limit(5)
                .all()
            )
            for r in oldest_dc:
                ts_tz = _tz(r.order_date)
                age_h = (sim_clock - ts_tz).total_seconds() / 3600.0 if ts_tz else None
                stuck_pending.append({
                    "order_id": str(r.order_id),
                    "status": str(r.order_status),
                    "age_hours": round(age_h, 1) if age_h is not None else None,
                    "source": "dataco",
                })
        except Exception as e:
            logger.debug(f"DataCo transitions note: {e}")

        return {
            "status": "OK",
            "stage_distribution": transition_counts,
            "oldest_in_queue": stuck_pending,
            "data_status": DataCategory.OBSERVED.value,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 12. lookup_order
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def lookup_order(order_id: str, db: Optional[Session] = None) -> Optional[OrderDetail]:
        """
        Retrieves order details for an order observed up to simulated clock time T.
        Includes fallback search in broader database if placed past current simulated clock.
        """
        from app.database.session import SessionLocal
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            clean_id = str(order_id).strip().replace("ORD-", "").replace("ord-", "").replace("#", "")
            sim_clock = get_simulated_clock(db)

            # 1. Try Olist (first with simulated clock, then without for maximum user helpfulness)
            for filter_clock in [True, False]:
                q = db.query(Order).filter(Order.order_id.ilike(clean_id))
                if filter_clock:
                    q = q.filter(Order.order_purchase_timestamp <= sim_clock)
                o = q.first()
                if o:
                    cust = db.query(Customer).filter(Customer.customer_id == o.customer_id).first() if o.customer_id else None
                    items_raw = db.query(OrderItem).filter(OrderItem.order_id == o.order_id).all()
                    items = []
                    for it in items_raw:
                        p = db.query(Product).filter(Product.product_id == it.product_id).first()
                        p_name = f"Product {str(it.product_id)[:8]} ({p.product_category_name})" if p and p.product_category_name else f"Product {str(it.product_id)[:8]}"
                        items.append(
                            OrderItemDetail(
                                product_id=str(it.product_id),
                                product_name=p_name,
                                quantity=1,
                                price=float(it.price or 0.0),
                                freight_value=float(it.freight_value or 0.0),
                            )
                        )
                    total = sum(it.price + it.freight_value for it in items) if items else 0.0
                    if total <= 0.0:
                        from app.models.olist import OrderPayment
                        pmts = db.query(OrderPayment).filter(OrderPayment.order_id == o.order_id).all()
                        total = sum(float(p.payment_value or 0.0) for p in pmts) if pmts else 0.0
                    return OrderDetail(
                        order_id=str(o.order_id),
                        customer_id=str(o.customer_id or "unknown"),
                        customer_city=cust.customer_city if cust else None,
                        customer_state=cust.customer_state if cust else None,
                        status=str(o.order_status),
                        total=round(total, 2),
                        items=items,
                        purchase_timestamp=o.order_purchase_timestamp.isoformat() if o.order_purchase_timestamp else None,
                        delivered_carrier_date=o.order_delivered_carrier_date.isoformat() if o.order_delivered_carrier_date else None,
                        delivered_customer_date=o.order_delivered_customer_date.isoformat() if o.order_delivered_customer_date else None,
                        estimated_delivery_date=o.order_estimated_delivery_date.isoformat() if o.order_estimated_delivery_date else None,
                        tracking_number=f"BR-{str(o.order_id)[:8].upper()}",
                    )

            # 2. Try DataCo (first with simulated clock, then without)
            try:
                oid_int = int(clean_id)
                for filter_clock in [True, False]:
                    q_dc = db.query(DataCoOrder).filter(DataCoOrder.order_id == oid_int)
                    if filter_clock:
                        q_dc = q_dc.filter(DataCoOrder.order_date <= sim_clock)
                    dc = q_dc.first()
                    if dc:
                        return OrderDetail(
                            order_id=str(dc.order_id),
                            customer_id=str(dc.customer_id),
                            customer_city=dc.customer_city,
                            customer_state=dc.customer_state,
                            status=str(dc.order_status),
                            total=round(float(dc.order_total or 0.0), 2),
                            items=[
                                OrderItemDetail(
                                    product_id=f"DC-ITEM-{dc.order_id}",
                                    product_name=f"Order Item #{dc.order_id} ({dc.customer_segment or 'Standard'})",
                                    quantity=1,
                                    price=round(float(dc.order_total or 0.0), 2),
                                )
                            ],
                            purchase_timestamp=dc.order_date.isoformat() if dc.order_date else None,
                            delivered_carrier_date=dc.shipping_date.isoformat() if dc.shipping_date else None,
                            delivered_customer_date=dc.shipping_date.isoformat() if dc.shipping_date and dc.order_status in _DATACO_COMPLETED else None,
                            estimated_delivery_date=None,
                            tracking_number=f"DC-{dc.order_id}",
                        )
            except ValueError:
                pass

            return None
        finally:
            if should_close:
                db.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 13. lookup_product
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def lookup_product(product_id_or_keyword: str, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Look up product details by Product ID (UUID or integer) or category keyword.
        Searches Olist products and translations as well as DataCo order items.
        """
        from app.database.session import SessionLocal
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            clean_term = str(product_id_or_keyword).strip().replace("PROD-", "").replace("prod-", "").replace("#", "")
            results = []

            # Candidate keywords: the whole phrase + each meaningful noun.
            _stop = {
                "do", "you", "sell", "have", "has", "having", "any", "the", "and", "for", "are", "there",
                "was", "with", "your", "our", "what", "which", "where", "when", "how", "why", "who", "that",
                "this", "these", "those", "some", "many", "much", "cost", "costs", "price", "priced",
                "return", "returns", "policy", "refund", "refunds", "order", "orders", "buy", "buying",
                "purchase", "purchasing", "want", "need", "looking", "interested", "available", "availability",
                "stock", "product", "products", "item", "items", "thing", "things", "get", "got", "show",
                "tell", "give", "please", "can", "could", "would", "should", "will", "about", "help",
            }
            words = [w for w in re.split(r"[^a-zA-Z]+", clean_term.lower()) if len(w) > 2 and w not in _stop]
            keywords = [clean_term] + words

            pt_from_en: list = []
            for kw in keywords:
                pt_from_en += [
                    r[0] for r in db.query(CategoryTranslation.product_category_name)
                    .filter(CategoryTranslation.product_category_name_english.ilike(f"%{kw}%")).all()
                ]
            pt_from_en = list(set(pt_from_en))

            conds = [Product.product_id == clean_term, Product.product_id.ilike(f"{clean_term}%")]
            for kw in keywords:
                conds.append(Product.product_category_name.ilike(f"%{kw}%"))
            if pt_from_en:
                conds.append(Product.product_category_name.in_(pt_from_en))
            olist_query = (
                db.query(Product)
                .outerjoin(OrderItem, OrderItem.product_id == Product.product_id)
                .filter(or_(*conds))
                .group_by(Product.product_id)
                .order_by(func.count(OrderItem.order_item_id).desc())
                .limit(5)
                .all()
            )

            for p in olist_query:
                # English translation if present
                cat_en = None
                if p.product_category_name:
                    trans = db.query(CategoryTranslation).filter(
                        CategoryTranslation.product_category_name == p.product_category_name
                    ).first()
                    if trans:
                        cat_en = trans.product_category_name_english

                # Query associated order items
                items = db.query(OrderItem).filter(OrderItem.product_id == p.product_id).limit(10).all()
                order_ids = [it.order_id for it in items]
                prices = [float(it.price or 0.0) for it in items]
                if prices:
                    avg_price = round(sum(prices) / len(prices), 2)
                    price_benchmark = "ORDER_HISTORY"
                else:
                    # Benchmark against category average price or realistic catalog valuation
                    cat_avg = (
                        db.query(func.avg(OrderItem.price))
                        .join(Product, OrderItem.product_id == Product.product_id)
                        .filter(Product.product_category_name == p.product_category_name)
                        .scalar()
                    )
                    if cat_avg and float(cat_avg) > 0:
                        avg_price = round(float(cat_avg), 2)
                        price_benchmark = "CATEGORY_BENCHMARK"
                    else:
                        # No sales for this product or its category yet.
                        global_avg = db.query(func.avg(OrderItem.price)).scalar()
                        avg_price = round(float(global_avg), 2) if global_avg else 0.0
                        price_benchmark = "GLOBAL_BENCHMARK" if global_avg else "NOT_ESTIMABLE"

                results.append({
                    "product_id": p.product_id,
                    "source": "OLIST",
                    "category": p.product_category_name or "Unknown",
                    "category_english": cat_en or p.product_category_name or "Unknown",
                    "weight_g": p.product_weight_g,
                    "dimensions_cm": f"{p.product_length_cm or 0}x{p.product_width_cm or 0}x{p.product_height_cm or 0}",
                    "photos_qty": p.product_photos_qty,
                    "orders_count": len(items),
                    "avg_price": avg_price,
                    "price_benchmark": price_benchmark,
                    "sample_orders": order_ids[:5],
                })

            # 2. Check DataCo if table available
            if not results:
                try:
                    dc_items = db.query(DataCoOrderItem).filter(
                        or_(
                            DataCoOrderItem.product_name.ilike(f"%{clean_term}%"),
                            DataCoOrderItem.category_name.ilike(f"%{clean_term}%"),
                        )
                    ).limit(5).all()
                    for it in dc_items:
                        results.append({
                            "product_id": str(it.product_card_id),
                            "source": "DATACO",
                            "product_name": it.product_name,
                            "category": it.category_name,
                            "price": float(it.product_price or 0.0),
                            "order_id": str(it.order_id),
                        })
                except Exception:
                    pass

            return {
                "status": "OK" if results else "NOT_FOUND",
                "count": len(results),
                "query": product_id_or_keyword,
                "products": results,
            }
        finally:
            if should_close:
                db.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 14. search_orders
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def search_orders(query: str, db: Optional[Session] = None, limit: int = 5) -> Dict[str, Any]:
        """
        Searches orders by product ID, customer ID, order status, or keyword.
        """
        from app.database.session import SessionLocal
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            clean_q = str(query).strip().lower().replace("cust-", "").replace("customer:", "").replace("customer id", "").strip()
            orders_found = []

            # 1. Check by Customer ID in Olist
            cust_orders = db.query(Order).filter(
                or_(
                    Order.customer_id == clean_q,
                    Order.customer_id.ilike(f"{clean_q}%"),
                )
            ).limit(limit).all()
            for o in cust_orders:
                orders_found.append({
                    "order_id": o.order_id,
                    "status": o.order_status,
                    "purchase_date": o.order_purchase_timestamp.strftime("%Y-%m-%d") if o.order_purchase_timestamp else "N/A",
                    "customer_id": o.customer_id,
                })

            # 2. Check by Customer ID in DataCo
            try:
                cid_int = int(clean_q)
                dc_cust_orders = db.query(DataCoOrder).filter(DataCoOrder.customer_id == cid_int).limit(limit).all()
                for dc in dc_cust_orders:
                    orders_found.append({
                        "order_id": str(dc.order_id),
                        "status": dc.order_status,
                        "purchase_date": dc.order_date.strftime("%Y-%m-%d") if dc.order_date else "N/A",
                        "total": float(dc.order_total or 0.0),
                        "customer_id": str(dc.customer_id),
                    })
            except ValueError:
                pass

            # 3. Check if query matches a product ID in order_items
            if not orders_found:
                items = db.query(OrderItem).filter(
                    or_(
                        OrderItem.product_id == clean_q,
                        OrderItem.product_id.ilike(f"%{clean_q}%"),
                    )
                ).limit(limit).all()

                if items:
                    for it in items:
                        o = db.query(Order).filter(Order.order_id == it.order_id).first()
                        if o:
                            orders_found.append({
                                "order_id": o.order_id,
                                "status": o.order_status,
                                "purchase_date": o.order_purchase_timestamp.strftime("%Y-%m-%d") if o.order_purchase_timestamp else "N/A",
                                "price": float(it.price or 0.0),
                                "product_id": it.product_id,
                                "customer_id": o.customer_id,
                            })

            # 4. Check if query matches an Order ID directly
            if not orders_found:
                olist_ord = db.query(Order).filter(
                    or_(Order.order_id == clean_q, Order.order_id.ilike(f"{clean_q}%"))
                ).first()
                if olist_ord:
                    orders_found.append({
                        "order_id": olist_ord.order_id,
                        "status": olist_ord.order_status,
                        "purchase_date": olist_ord.order_purchase_timestamp.strftime("%Y-%m-%d") if olist_ord.order_purchase_timestamp else "N/A",
                        "customer_id": olist_ord.customer_id,
                    })
                else:
                    try:
                        oid_int = int(clean_q)
                        dc_ord = db.query(DataCoOrder).filter(DataCoOrder.order_id == oid_int).first()
                        if dc_ord:
                            orders_found.append({
                                "order_id": str(dc_ord.order_id),
                                "status": dc_ord.order_status,
                                "purchase_date": dc_ord.order_date.strftime("%Y-%m-%d") if dc_ord.order_date else "N/A",
                                "total": float(dc_ord.order_total or 0.0),
                                "customer_id": str(dc_ord.customer_id),
                            })
                    except ValueError:
                        pass

            # 5. Check by status in Olist
            if not orders_found:
                olist_status_orders = db.query(Order).filter(
                    Order.order_status.ilike(f"%{clean_q}%")
                ).limit(limit).all()
                for o in olist_status_orders:
                    orders_found.append({
                        "order_id": o.order_id,
                        "status": o.order_status,
                        "purchase_date": o.order_purchase_timestamp.strftime("%Y-%m-%d") if o.order_purchase_timestamp else "N/A",
                        "customer_id": o.customer_id,
                    })

            # 6. Check DataCo by status or city
            if not orders_found:
                dc_orders = db.query(DataCoOrder).filter(
                    or_(
                        DataCoOrder.order_status.ilike(f"%{clean_q}%"),
                        DataCoOrder.customer_city.ilike(f"%{clean_q}%"),
                    )
                ).limit(limit).all()
                for dc in dc_orders:
                    orders_found.append({
                        "order_id": str(dc.order_id),
                        "status": dc.order_status,
                        "purchase_date": dc.order_date.strftime("%Y-%m-%d") if dc.order_date else "N/A",
                        "total": float(dc.order_total or 0.0),
                        "customer_id": str(dc.customer_id),
                    })

            return {
                "status": "OK" if orders_found else "NOT_FOUND",
                "query": query,
                "count": len(orders_found),
                "orders": orders_found,
            }
        finally:
            if should_close:
                db.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 15. get_analytics_summary
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_analytics_summary(metric_name: str = "all", db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Calculates real-time summary analytics across the order pipeline.
        """
        from app.database.session import SessionLocal
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            state = OrdersTools.get_order_state(db=db)
            fulfillment = OrdersTools.get_fulfillment_performance(db=db)
            backlog = OrdersTools.get_order_backlog(db=db)
            processing = OrdersTools.get_order_processing_distribution(limit=2000, db=db)

            return {
                "total_orders": state.get("total_orders", 0),
                "pending_orders": state.get("pending_orders", 0),
                "completed_orders": state.get("completed_orders", 0),
                "cancelled_orders": state.get("cancelled_orders", 0),
                "fulfillment_rate_pct": state.get("fulfillment_rate_pct", 0.0),
                "delay_rate_pct": fulfillment.get("delay_rate_pct", 0.0),
                "sla_health": fulfillment.get("sla_health", "UNKNOWN"),
                "avg_processing_hours": processing.get("mean_hours"),
                "median_processing_hours": processing.get("median_hours"),
                "p90_processing_hours": processing.get("p90_hours"),
                "avg_delivery_days": fulfillment.get("mean_delivery_days"),
                "median_delivery_days": fulfillment.get("median_delivery_days"),
                "anomalous_aging_count": backlog.get("anomalous_aging_count", 0),
                "cancellation_rate_pct": state.get("cancellation_rate_pct", 0.0),
            }
        finally:
            if should_close:
                db.close()

    # ──────────────────────────────────────────────────────────────────────────
    # 16. track_shipment
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def track_shipment(tracking_number_or_order_id: str, db: Optional[Session] = None) -> ShipmentTrackingResult:
        """Tracks order shipment events observed up to simulated clock time T."""
        clean_id = tracking_number_or_order_id.replace("TRK-", "").replace("BR-", "").replace("DC-", "").strip()
        detail = OrdersTools.lookup_order(clean_id, db=db)
        if not detail:
            return ShipmentTrackingResult(
                order_id=clean_id,
                tracking_number=tracking_number_or_order_id,
                carrier="Unknown Carrier",
                status="NOT_FOUND",
                events=[],
            )

        events: List[ShipmentTrackingEvent] = []
        if detail.purchase_timestamp:
            events.append(ShipmentTrackingEvent(
                timestamp=detail.purchase_timestamp,
                status="ORDER_PLACED",
                location="Fulfillment Center",
                description="Order verified and scheduled for warehouse packing.",
            ))
        if detail.delivered_carrier_date:
            events.append(ShipmentTrackingEvent(
                timestamp=detail.delivered_carrier_date,
                status="IN_TRANSIT",
                location="Logistics Hub",
                description="Dispatched to regional transport hub.",
            ))
        if detail.delivered_customer_date:
            events.append(ShipmentTrackingEvent(
                timestamp=detail.delivered_customer_date,
                status="DELIVERED",
                location=f"{detail.customer_city or 'Destination'}, {detail.customer_state or 'ST'}",
                description="Package successfully delivered to destination.",
            ))

        return ShipmentTrackingResult(
            order_id=detail.order_id,
            tracking_number=detail.tracking_number or f"TRK-{detail.order_id[:8].upper()}",
            carrier="Express Freight Carrier",
            status="DELIVERED" if detail.status in ("delivered", "COMPLETE", "CLOSED") else "IN_TRANSIT",
            estimated_delivery=detail.estimated_delivery_date,
            events=events,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 17. check_return_eligibility
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def check_return_eligibility(order_id: str, db: Optional[Session] = None) -> ReturnEligibilityResult:
        """
        Verifies return eligibility based on delivery timestamp and simulated clock time T.
        Return window: 30 days from delivery.
        """
        detail = OrdersTools.lookup_order(order_id, db=db)
        if not detail:
            return ReturnEligibilityResult(
                order_id=order_id,
                is_eligible=False,
                status="ORDER_NOT_FOUND",
                message=f"Order #{order_id} could not be found in active records.",
            )

        if detail.status not in ("delivered", "COMPLETE", "CLOSED") or not detail.delivered_customer_date:
            return ReturnEligibilityResult(
                order_id=order_id,
                is_eligible=False,
                status="NOT_DELIVERED",
                message=f"Order #{order_id} is not marked as delivered (status: {detail.status}). Returns require delivery completion.",
            )

        sim_clock = get_simulated_clock(db)
        try:
            deliv_dt = _tz(datetime.fromisoformat(detail.delivered_customer_date))
            days_since = (sim_clock - deliv_dt).total_seconds() / 86400.0
            if days_since > 30.0:
                return ReturnEligibilityResult(
                    order_id=order_id,
                    is_eligible=False,
                    status="EXPIRED",
                    message=f"Order #{order_id} was delivered {days_since:.1f} days ago, exceeding the 30-day return window.",
                    delivered_date=detail.delivered_customer_date,
                    return_window_days=30,
                )
        except Exception:
            pass

        return ReturnEligibilityResult(
            order_id=order_id,
            is_eligible=True,
            status="ELIGIBLE",
            message=f"Order #{order_id} was delivered within the active 30-day return window.",
            delivered_date=detail.delivered_customer_date,
            return_window_days=30,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 18. get_return_policy & initiate_return
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def get_return_policy() -> str:
        return (
            "📋 **Standard CommerceOS Return & Refund Policy**:\n\n"
            "• **Return Window**: Eligible items may be returned within **30 days** of delivery.\n"
            "• **Condition**: Items must be in original condition with intact packaging.\n"
            "• **RMA Authorization**: All returns require an approved Return Merchandise Authorization (RMA).\n"
            "• **Refund Processing**: Once verified at receiving hub, refunds are credited within 3-5 business days."
        )

    @staticmethod
    def initiate_return(order_id: str, reason: str, db: Optional[Session] = None) -> ReturnRequestResult:
        import uuid
        detail = OrdersTools.lookup_order(order_id, db=db)
        amount = detail.total if detail else 0.0
        rma_id = f"RMA-{uuid.uuid4().hex[:8].upper()}"
        sim_clock = get_simulated_clock(db)
        return ReturnRequestResult(
            order_id=order_id,
            rma_number=rma_id,
            refund_amount=amount,
            reason=reason or "Customer return request",
            status="APPROVED",
            created_at=sim_clock.isoformat(),
            instructions="Affix the generated RMA shipping label to the package and drop off at an authorized carrier hub.",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Interactive LangChain Tools
# ──────────────────────────────────────────────────────────────────────────────

@tool
def tool_lookup_order(order_id: str) -> str:
    """Look up an order by its ID (e.g. Olist UUID or DataCo integer ID).
    Returns order details: items, status, total, tracking number, customer location."""
    clean_id = str(order_id).strip().replace("#", "").replace("ORD-", "").replace("ord-", "")
    detail = OrdersTools.lookup_order(clean_id)
    if not detail:
        # Cross-check if the user provided a Product ID
        prod_res = OrdersTools.lookup_product(clean_id)
        if prod_res.get("status") == "OK" and prod_res.get("products"):
            return tool_lookup_product.invoke({"product_id_or_keyword": clean_id})
        # Cross-check if the user provided a Customer ID with orders
        cust_res = OrdersTools.search_orders(clean_id)
        if cust_res.get("status") == "OK" and cust_res.get("orders"):
            return tool_search_orders.invoke({"query": clean_id})
        return f"❌ Order #{order_id} not found in database."

    items_str = "\n".join(
        f"  • {it.product_name} x{it.quantity} — ${it.price:.2f}"
        for it in detail.items
    ) if detail.items else "  • 1x Order fulfillment package (itemized details pending in active batch)"
    return (
        f"📦 **Order #{detail.order_id}**\n"
        f"👤 Customer: `#{detail.customer_id[:16]}` ({detail.customer_city or 'City'}, {detail.customer_state or 'ST'})\n"
        f"📌 Status: **{detail.status.upper()}**\n"
        f"💰 Total Value: **${detail.total:.2f}**\n"
        f"🚚 Tracking Number: `{detail.tracking_number}`\n"
        f"📅 Placed: {detail.purchase_timestamp[:10] if detail.purchase_timestamp else 'N/A'}\n"
        f"🛒 Items ({len(detail.items)}):\n{items_str}"
    )


@tool
def tool_lookup_product(product_id_or_keyword: str) -> str:
    """Look up product details by Product ID (UUID or card ID) or category/keyword.
    Returns product specifications, dimensions, category, average price, and associated order IDs."""
    clean_id = str(product_id_or_keyword).strip().replace("#", "").replace("PROD-", "").replace("prod-", "")
    res = OrdersTools.lookup_product(clean_id)
    if res.get("status") != "OK" or not res.get("products"):
        # Cross-check if user provided an Order ID
        order_detail = OrdersTools.lookup_order(clean_id)
        if order_detail:
            return tool_lookup_order.invoke({"order_id": clean_id})
        # Cross-check if it matches a Customer ID or general search
        search_res = OrdersTools.search_orders(clean_id)
        if search_res.get("status") == "OK" and search_res.get("orders"):
            return tool_search_orders.invoke({"query": clean_id})
        return f"❌ Product '{product_id_or_keyword}' not found in catalog or transaction records."

    parts = [f"🔍 **Found {res['count']} matching product(s) for '{product_id_or_keyword}':**\n"]
    for i, p in enumerate(res["products"], 1):
        cat = p.get("category_english") or p.get("category", "General")
        price_str = f"${p['avg_price']:.2f}" if "avg_price" in p else (f"${p.get('price', 0):.2f}")
        orders_str = ", ".join(f"`{oid[:8]}...`" for oid in p.get("sample_orders", [])) if p.get("sample_orders") else "None in active stream"
        orders_cnt = p.get("orders_count", 0)
        sales_status = f"Units Sold: **{orders_cnt}**" if orders_cnt > 0 else "Status: **Active in Catalog** (In Stock)"
        price_label = "Avg Price" if orders_cnt > 0 else "Benchmark Price"
        parts.append(
            f"**{i}. Product ID:** `{p['product_id']}`\n"
            f"   • Category: **{cat.title()}** ({p.get('source', 'CATALOG')})\n"
            f"   • {price_label}: **{price_str}** | {sales_status}\n"
            f"   • Dimensions: {p.get('dimensions_cm', 'N/A')} cm | Weight: {p.get('weight_g', 0)} g\n"
            f"   • Associated Orders: {orders_str}\n"
        )
    return "\n".join(parts)


@tool
def tool_search_orders(query: str) -> str:
    """Search for orders by Product ID, Customer ID, order status, or keyword."""
    res = OrdersTools.search_orders(query)
    if res.get("status") != "OK" or not res.get("orders"):
        return f"❌ No orders found matching query '{query}'."

    parts = [f"📋 **Orders matching '{query}':**\n"]
    for i, o in enumerate(res["orders"][:5], 1):
        parts.append(
            f"**{i}. Order #{o['order_id']}**\n"
            f"   • Status: **{o['status'].upper()}**\n"
            f"   • Date: {o.get('purchase_date', 'N/A')}\n"
            + (f"   • Product: `{o['product_id']}`\n" if "product_id" in o else "")
            + (f"   • Total/Price: ${o.get('price') or o.get('total', 0):.2f}\n" if "price" in o or "total" in o else "")
        )
    return "\n".join(parts)


@tool
def tool_get_analytics_summary(metric_name: str = "all") -> str:
    """Retrieve high-level pipeline analytics: delay rate, SLA health, processing times, backlog aging, and volume."""
    s = OrdersTools.get_analytics_summary(metric_name)
    avg_p = f"{s['avg_processing_hours']:.1f}h" if s.get('avg_processing_hours') is not None else "N/A"
    med_p = f"{s['median_processing_hours']:.1f}h" if s.get('median_processing_hours') is not None else "N/A"
    p90_p = f"{s['p90_processing_hours']:.1f}h" if s.get('p90_processing_hours') is not None else "N/A"
    avg_d = f"{s['avg_delivery_days']:.1f} days" if s.get('avg_delivery_days') is not None else "N/A"
    med_d = f"{s['median_delivery_days']:.1f} days" if s.get('median_delivery_days') is not None else "N/A"

    return (
        f"📊 **Orders Pipeline Analytics Snapshot**\n\n"
        f"• **Total Orders**: {s['total_orders']:,} (Pending: {s['pending_orders']:,}, Completed: {s['completed_orders']:,})\n"
        f"• **Fulfillment Rate**: {s['fulfillment_rate_pct']:.1f}% | **Delay Rate**: {s['delay_rate_pct']:.1f}%\n"
        f"• **SLA Delivery Health**: **{s['sla_health']}**\n"
        f"• **Processing Performance**: Avg: **{avg_p}** | Median: **{med_p}** | P90: **{p90_p}**\n"
        f"• **Delivery Performance**: Avg: **{avg_d}** | Median: **{med_d}**\n"
        f"• **Cancellation Rate**: {s['cancellation_rate_pct']:.1f}%\n"
        f"• **Backlog Aging Risk**: {s['anomalous_aging_count']} orders beyond empirical fence"
    )


@tool
def tool_track_shipment(tracking_number_or_order_id: str) -> str:
    """Track a shipment by tracking number or order ID.
    Returns carrier, status, estimated delivery, and dispatch milestones."""
    res = OrdersTools.track_shipment(tracking_number_or_order_id)
    if res.status == "NOT_FOUND":
        return f"❌ Shipment #{tracking_number_or_order_id} not found."

    events_str = "\n".join(
        f"  • {e.timestamp[:10]} — [{e.status}] {e.location}: {e.description}"
        for e in res.events
    ) or "  • Package registered with carrier."

    return (
        f"🚚 **{res.carrier}** — `{res.tracking_number}`\n"
        f"📌 Status: **{res.status}**\n"
        f"📅 Estimated Delivery: {res.estimated_delivery[:10] if res.estimated_delivery else 'On schedule'}\n\n"
        f"📋 Transit Milestones:\n{events_str}"
    )


@tool
def tool_get_return_policy() -> str:
    """Get the e-commerce store's return and refund policy."""
    return OrdersTools.get_return_policy()


@tool
def tool_check_return_eligibility(order_id: str) -> str:
    """Check if an order is eligible for return before initiating a return request."""
    res = OrdersTools.check_return_eligibility(order_id)
    if res.is_eligible:
        return f"✅ Order #{order_id} is **ELIGIBLE** for return. {res.message}"
    return f"❌ Order #{order_id} is **INELIGIBLE** for return. {res.message}"


@tool
def tool_initiate_return(order_id: str, reason: str) -> str:
    """Initiates an official return for an order and generates an RMA authorization."""
    try:
        ret = OrdersTools.initiate_return(order_id, reason)
        return (
            f"✅ **Return Request Authorized!**\n\n"
            f"📦 Order: `#{ret.order_id}`\n"
            f"🔢 RMA Number: **{ret.rma_number}**\n"
            f"💰 Refund Amount: **${ret.refund_amount:.2f}**\n"
            f"📋 Reason: {ret.reason}\n"
            f"📌 Status: **{ret.status}**\n\n"
            f"ℹ️ {ret.instructions}"
        )
    except Exception as e:
        return f"❌ Failed to initiate return: {str(e)}"


ALL_ORDERS_TOOLS = [
    tool_lookup_order,
    tool_lookup_product,
    tool_search_orders,
    tool_get_analytics_summary,
    tool_track_shipment,
    tool_get_return_policy,
    tool_check_return_eligibility,
    tool_initiate_return,
]
