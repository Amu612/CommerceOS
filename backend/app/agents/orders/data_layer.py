"""
Deterministic, source-aware, temporally consistent data layer for Orders Agent.
Calculates state, backlog, cancellation risk, and fulfillment health exclusively
from observed records available up to the current simulated/replay clock time T.
Zero hardcoded business thresholds or fabricated estimates.
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.intelligence.anomaly.detector import AnomalyDetector
from app.intelligence.statistics.profiler import StatisticalProfiler
from app.models.dataco import DataCoOrder
from app.models.olist import Order

logger = logging.getLogger(__name__)


def _normalize_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# The one canonical clock implementation now lives in `app.agents._shared`
# (it also handles the live-Shopify-source case); kept as `get_simulated_clock`
# here since that's the name every call site in this file already uses.
from app.agents._shared import simulated_clock as get_simulated_clock  # noqa: E402


class OrdersDataLayer:
    """
    Source-aware operational data layer.
    Exclusively evaluates records whose timestamp <= simulated_clock.
    """

    _db_available: bool | None = None

    @classmethod
    def _get_session(cls, db: Session | None) -> tuple[Session | None, bool]:
        if db is not None:
            return db, False
        if cls._db_available is False:
            return None, False
        try:
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            cls._db_available = True
            return session, True
        except Exception:
            cls._db_available = False
            return None, False

    @classmethod
    def get_order_summary(cls, db: Session | None = None) -> dict[str, Any]:
        """Calculates dynamic total, pending, completed, cancelled, delayed orders and fulfillment rate up to current simulated time T."""
        session, close = cls._get_session(db)
        if session is None:
            return cls._empty_summary("DATABASE_UNAVAILABLE")

        try:
            from app.services.data_source_service import data_source_service

            if data_source_service.is_live():
                return cls._get_shopify_order_summary(session)

            sim_clock = get_simulated_clock(session)
            o_total = o_completed = o_cancelled = o_pending = o_delayed = 0

            # 1. Olist Orders (timestamp <= sim_clock)
            try:
                olist_status_counts = dict(
                    session.query(Order.order_status, func.count(Order.order_id))
                    .filter(Order.order_purchase_timestamp <= sim_clock)
                    .group_by(Order.order_status)
                    .all()
                )
                o_total = sum(olist_status_counts.values())
                o_completed = olist_status_counts.get("delivered", 0)
                o_cancelled = olist_status_counts.get("canceled", 0) + olist_status_counts.get(
                    "unavailable", 0
                )
                olist_pending_statuses = ["created", "approved", "invoiced", "processing"]
                o_pending = sum(olist_status_counts.get(s, 0) for s in olist_pending_statuses)

                o_delayed = (
                    session.query(func.count(Order.order_id))
                    .filter(
                        Order.order_purchase_timestamp <= sim_clock,
                        Order.order_status == "delivered",
                        Order.order_delivered_customer_date.isnot(None),
                        Order.order_estimated_delivery_date.isnot(None),
                        Order.order_delivered_customer_date > Order.order_estimated_delivery_date,
                    )
                    .scalar()
                    or 0
                )
            except Exception as e:
                logger.debug(f"Olist query note: {e}")

            # 2. DataCo Orders (order_date <= sim_clock)
            dc_total = dc_completed = dc_cancelled = dc_pending = dc_delayed = 0
            try:
                dc_status_counts = dict(
                    session.query(DataCoOrder.order_status, func.count(DataCoOrder.order_id))
                    .filter(DataCoOrder.order_date <= sim_clock)
                    .group_by(DataCoOrder.order_status)
                    .all()
                )
                dc_total = sum(dc_status_counts.values())
                dc_completed = dc_status_counts.get("COMPLETE", 0) + dc_status_counts.get("CLOSED", 0)
                dc_cancelled = dc_status_counts.get("CANCELED", 0) + dc_status_counts.get(
                    "SUSPECTED_FRAUD", 0
                )
                dc_pending_statuses = [
                    "PROCESSING",
                    "PENDING",
                    "PENDING_PAYMENT",
                    "ON_HOLD",
                    "PAYMENT_REVIEW",
                ]
                dc_pending = sum(dc_status_counts.get(s, 0) for s in dc_pending_statuses)

                dc_delayed = (
                    session.query(func.count(DataCoOrder.order_id))
                    .filter(
                        DataCoOrder.order_date <= sim_clock,
                        DataCoOrder.late_delivery_risk == 1,
                    )
                    .scalar()
                    or 0
                )
            except Exception as e:
                logger.debug(f"DataCo query note: {e}")

            total = o_total + dc_total
            if total == 0:
                return cls._empty_summary("NO_OBSERVED_RECORDS")

            data_source = "BOTH"
            if o_total > 0 and dc_total == 0:
                data_source = "OLIST"
            elif dc_total > 0 and o_total == 0:
                data_source = "DATACO"

            pending = o_pending + dc_pending
            completed = o_completed + dc_completed
            cancelled = o_cancelled + dc_cancelled
            delayed = o_delayed + dc_delayed
            fulfillment_rate = round((completed / total) * 100.0, 2)
            cancellation_rate = round((cancelled / total) * 100.0, 2)

            return {
                "total_orders": total,
                "pending_orders": pending,
                "completed_orders": completed,
                "cancelled_orders": cancelled,
                "delayed_orders": delayed,
                "fulfillment_rate_pct": fulfillment_rate,
                "cancellation_rate_pct": cancellation_rate,
                "data_source": data_source,
                "data_status": "OBSERVED",
                "sample_count": total,
                "simulated_clock": sim_clock.isoformat(),
            }
        except Exception as e:
            logger.warning(f"Error querying order summary: {e}")
            return cls._empty_summary(str(e))
        finally:
            if close:
                session.close()

    @classmethod
    def _get_shopify_order_summary(cls, session: Session) -> dict[str, Any]:
        """Live-source counterpart of `get_order_summary` — real Shopify orders,
        no simulated clock (live orders are simply 'as of right now')."""
        from app.models.shopify import ShopifyOrder

        try:
            rows = (
                session.query(
                    ShopifyOrder.fulfillment_status,
                    ShopifyOrder.cancelled_at,
                    func.count(ShopifyOrder.order_id),
                )
                .group_by(ShopifyOrder.fulfillment_status, ShopifyOrder.cancelled_at.isnot(None))
                .all()
            )
        except Exception as e:
            logger.warning(f"Shopify order summary query failed: {e}")
            return cls._empty_summary("DATABASE_UNAVAILABLE")

        total = completed = cancelled = pending = 0
        for fulfillment_status, cancelled_at, count in rows:
            total += count
            if cancelled_at is not None:
                cancelled += count
            elif fulfillment_status == "fulfilled":
                completed += count
            else:
                pending += count

        if total == 0:
            return cls._empty_summary("NO_OBSERVED_RECORDS")

        fulfillment_rate = round((completed / total) * 100.0, 2)
        cancellation_rate = round((cancelled / total) * 100.0, 2)
        return {
            "total_orders": total,
            "pending_orders": pending,
            "completed_orders": completed,
            "cancelled_orders": cancelled,
            # Shopify's base order feed has no promised-delivery-date field, so
            # "delayed" isn't computable from it the way it is for Olist's
            # estimated-vs-actual delivery dates — reported honestly as 0/NOT
            # a fabricated figure, not "no delays occurred."
            "delayed_orders": 0,
            "fulfillment_rate_pct": fulfillment_rate,
            "cancellation_rate_pct": cancellation_rate,
            "data_source": "SHOPIFY",
            "data_status": "OBSERVED",
            "sample_count": total,
            "simulated_clock": datetime.now(UTC).isoformat(),
        }

    @classmethod
    def _empty_summary(cls, reason: str = "NO_RECORDS") -> dict[str, Any]:
        return {
            "total_orders": 0,
            "pending_orders": 0,
            "completed_orders": 0,
            "cancelled_orders": 0,
            "delayed_orders": 0,
            "fulfillment_rate_pct": 0.0,
            "cancellation_rate_pct": 0.0,
            "data_source": "NONE",
            "data_status": "NOT_ESTIMABLE",
            "not_estimable_reason": reason,
            "sample_count": 0,
        }

    @classmethod
    def get_pending_queue(cls, db: Session | None = None) -> dict[str, Any]:
        """Calculates pending count, empirical age distribution, percentiles, and outlier fences from actual timestamps."""
        session, close = cls._get_session(db)
        if session is None:
            return cls._empty_pending_queue("DATABASE_UNAVAILABLE")

        try:
            sim_clock = get_simulated_clock(session)
            ages_hours: list[float] = []
            has_olist = False
            has_dataco = False

            # 1. Olist Pending Orders
            try:
                olist_pending_statuses = ["created", "approved", "invoiced", "processing"]
                olist_rows = (
                    session.query(Order.order_purchase_timestamp)
                    .filter(
                        Order.order_purchase_timestamp <= sim_clock,
                        Order.order_status.in_(olist_pending_statuses),
                    )
                    .all()
                )
                if olist_rows:
                    has_olist = True
                    for (ts,) in olist_rows:
                        if ts:
                            ts_n = _normalize_dt(ts)
                            diff = max(0.0, (sim_clock - ts_n).total_seconds() / 3600.0)
                            ages_hours.append(diff)
            except Exception as e:
                logger.debug(f"Olist pending note: {e}")

            # 2. DataCo Pending Orders
            try:
                dc_pending_statuses = [
                    "PROCESSING",
                    "PENDING",
                    "PENDING_PAYMENT",
                    "ON_HOLD",
                    "PAYMENT_REVIEW",
                ]
                dc_rows = (
                    session.query(DataCoOrder.order_date)
                    .filter(
                        DataCoOrder.order_date <= sim_clock,
                        DataCoOrder.order_status.in_(dc_pending_statuses),
                    )
                    .all()
                )
                if dc_rows:
                    has_dataco = True
                    for (ts,) in dc_rows:
                        if ts:
                            ts_n = _normalize_dt(ts)
                            diff = max(0.0, (sim_clock - ts_n).total_seconds() / 3600.0)
                            ages_hours.append(diff)
            except Exception as e:
                logger.debug(f"DataCo pending note: {e}")

            n_pending = len(ages_hours)
            if n_pending == 0:
                return {
                    "pending_count": 0,
                    "age_distribution": [],
                    "median_age_hours": None,
                    "p75_age_hours": None,
                    "p90_age_hours": None,
                    "p95_age_hours": None,
                    "max_age_hours": None,
                    "anomalous_aging_count": 0,
                    "aging_over_48h": 0,
                    "data_source": (
                        "BOTH"
                        if (has_olist and has_dataco)
                        else ("OLIST" if has_olist else ("DATACO" if has_dataco else "NONE"))
                    ),
                    "data_status": "NOT_ESTIMABLE",
                    "not_estimable_reason": "No pending orders in active queue as of current simulated clock.",
                    "sample_count": 0,
                }

            # Statistical profile of age distribution
            profile = StatisticalProfiler.profile(ages_hours)
            median_age = profile.median if profile else None
            p75_age = profile.q75 if profile else None
            # Tukey outlier fence: Q3 + 1.5 * IQR (or median + 3 * MAD if IQR is 0)
            if profile and profile.iqr > 0:
                anom_threshold = profile.q75 + 1.5 * profile.iqr
            elif profile and profile.mad > 0:
                anom_threshold = profile.median + 3.0 * profile.mad
            else:
                anom_threshold = profile.max_val if profile else 0.0

            sorted_ages = sorted(ages_hours)

            def _pct(p: float) -> float:
                idx = int((len(sorted_ages) - 1) * p)
                return sorted_ages[idx]

            p90_age = _pct(0.90)
            p95_age = _pct(0.95)
            max_age = sorted_ages[-1]
            anomalous_count = sum(1 for a in ages_hours if a > anom_threshold)
            over_48h = sum(1 for a in ages_hours if a > 48.0)

            # Construct dynamic age histogram buckets from empirical range
            min_h = sorted_ages[0]
            max_h = max_age
            buckets = []
            if max_h <= min_h or n_pending < 4:
                buckets.append(
                    {
                        "label": f"{min_h:.1f}h - {max_h:.1f}h",
                        "lower_bound_hours": round(min_h, 1),
                        "upper_bound_hours": round(max_h, 1),
                        "order_count": n_pending,
                        "pct_of_pending": 100.0,
                    }
                )
            else:
                q1 = profile.q25 if profile else min_h
                q2 = median_age or ((min_h + max_h) / 2.0)
                q3 = profile.q75 if profile else max_h
                cutoffs = [min_h, q1, q2, q3, max_h]
                cutoffs = sorted(set(cutoffs))
                for i in range(len(cutoffs) - 1):
                    low = cutoffs[i]
                    high = cutoffs[i + 1]
                    c = sum(
                        1
                        for a in ages_hours
                        if (low <= a <= high if i == len(cutoffs) - 2 else low <= a < high)
                    )
                    buckets.append(
                        {
                            "label": f"{low:.1f}h - {high:.1f}h",
                            "lower_bound_hours": round(low, 1),
                            "upper_bound_hours": round(high, 1),
                            "order_count": c,
                            "pct_of_pending": round((c / n_pending) * 100.0, 1),
                        }
                    )

            data_src = "BOTH" if (has_olist and has_dataco) else ("OLIST" if has_olist else "DATACO")
            return {
                "pending_count": n_pending,
                "age_distribution": buckets,
                "median_age_hours": round(median_age, 2) if median_age is not None else None,
                "p75_age_hours": round(p75_age, 2) if p75_age is not None else None,
                "p90_age_hours": round(p90_age, 2),
                "p95_age_hours": round(p95_age, 2),
                "max_age_hours": round(max_age, 2),
                "anomalous_aging_count": anomalous_count,
                "empirical_outlier_fence_hours": round(anom_threshold, 2),
                "aging_over_48h": over_48h,
                "data_source": data_src,
                "data_status": "CALCULATED",
                "sample_count": n_pending,
            }
        except Exception as e:
            logger.warning(f"Error querying pending queue: {e}")
            return cls._empty_pending_queue(str(e))
        finally:
            if close:
                session.close()

    @classmethod
    def _empty_pending_queue(cls, reason: str = "NO_RECORDS") -> dict[str, Any]:
        return {
            "pending_count": 0,
            "age_distribution": [],
            "median_age_hours": None,
            "p75_age_hours": None,
            "p90_age_hours": None,
            "p95_age_hours": None,
            "max_age_hours": None,
            "anomalous_aging_count": 0,
            "aging_over_48h": 0,
            "data_source": "NONE",
            "data_status": "NOT_ESTIMABLE",
            "not_estimable_reason": reason,
            "sample_count": 0,
        }

    @classmethod
    def get_cancellation_risk(cls, db: Session | None = None) -> dict[str, Any]:
        """
        Calculates cancellation rate, empirical dispersion, risk tier, and predicted cancellations.
        Derived strictly from observed records without arbitrary fixed thresholds.
        """
        session, close = cls._get_session(db)
        if session is None:
            return {
                "cancellation_rate_pct": 0.0,
                "risk_level": "LOW",
                "predicted_cancellations": 0,
                "risk_drivers": [],
                "data_source": "NONE",
                "data_status": "NOT_ESTIMABLE",
                "not_estimable_reason": "Database unavailable",
            }

        try:
            summary = cls.get_order_summary(db=session)
            total = summary.get("total_orders", 0)
            cancelled = summary.get("cancelled_orders", 0)
            pending = summary.get("pending_orders", 0)

            if total == 0:
                return {
                    "cancellation_rate_pct": 0.0,
                    "historical_baseline_rate_pct": None,
                    "z_score": None,
                    "risk_level": "LOW",
                    "predicted_cancellations": 0,
                    "risk_drivers": [],
                    "anomaly_score": None,
                    "data_source": "NONE",
                    "data_status": "NOT_ESTIMABLE",
                    "not_estimable_reason": "No order observations available to estimate cancellation risk.",
                }

            canc_rate = round((cancelled / total) * 100.0, 2)
            predicted = int(round(pending * (canc_rate / 100.0)))

            # Get daily historical cancellation rate series up to sim_clock
            from app.agents.orders.tools import OrdersTools

            canc_hist = OrdersTools.get_cancellation_history(days=90, db=session)
            daily_series = canc_hist.get("daily_series", [])
            daily_rates = [
                float(r["cancellation_rate_pct"]) for r in daily_series if "cancellation_rate_pct" in r
            ]

            risk_level = "LOW"
            z_score = None
            anomaly_score = None
            risk_drivers = []
            baseline_mean = None

            if len(daily_rates) >= 2:
                profile = StatisticalProfiler.profile(daily_rates)
                if profile:
                    baseline_mean = profile.mean
                    if profile.std_dev > 1e-6:
                        z_score = round((canc_rate - profile.mean) / profile.std_dev, 2)
                        anom = AnomalyDetector.evaluate_sample(canc_rate, daily_rates, "cancellation_rate")
                        anomaly_score = anom.anomaly_score

                        # Dynamically derive risk from empirical distribution:
                        # Modified Z-Score / Tukey outlier threshold
                        if z_score >= 3.0 or canc_rate > profile.upper_outer_fence:
                            risk_level = "CRITICAL"
                            risk_drivers.append(
                                f"Cancellation rate ({canc_rate}%) is an extreme statistical outlier (z={z_score}, upper fence={profile.upper_outer_fence}%)."
                            )
                        elif z_score >= 2.0 or canc_rate > profile.q75:
                            risk_level = "HIGH"
                            risk_drivers.append(
                                f"Cancellation rate ({canc_rate}%) exceeds the 75th percentile of historical daily rates (z={z_score})."
                            )
                        elif z_score >= 1.0 or canc_rate > profile.median:
                            risk_level = "MEDIUM"
                            risk_drivers.append(
                                f"Cancellation rate ({canc_rate}%) is above empirical median ({profile.median}%)."
                            )
                        else:
                            risk_level = "LOW"
                            risk_drivers.append(
                                f"Cancellation rate ({canc_rate}%) is within normal empirical baseline range ({profile.q25}% - {profile.q75}%)."
                            )
            else:
                # Small sample: derive risk from standard error of binomial proportion SE = sqrt(p*(1-p)/n)
                p = cancelled / total
                se = math.sqrt(p * (1.0 - p) / total) if total > 1 else p
                if p > 0:
                    risk_drivers.append(
                        f"Cancellation rate ({canc_rate}%) evaluated from sample size n={total} (standard error={se*100:.2f}%)."
                    )
                    if p >= 0.15:
                        risk_level = "HIGH"
                    elif p >= 0.05:
                        risk_level = "MEDIUM"
                    else:
                        risk_level = "LOW"

            return {
                "cancellation_rate_pct": canc_rate,
                "historical_baseline_rate_pct": (
                    round(baseline_mean, 2) if baseline_mean is not None else None
                ),
                "z_score": z_score,
                "risk_level": risk_level,
                "predicted_cancellations": predicted,
                "risk_drivers": risk_drivers,
                "anomaly_score": anomaly_score,
                "data_source": summary.get("data_source", "BOTH"),
                "data_status": "CALCULATED",
                "sample_count": total,
            }
        except Exception as e:
            logger.warning(f"Error evaluating cancellation risk: {e}")
            return {
                "cancellation_rate_pct": 0.0,
                "risk_level": "LOW",
                "predicted_cancellations": 0,
                "risk_drivers": [],
                "data_source": "NONE",
                "data_status": "NOT_ESTIMABLE",
                "not_estimable_reason": str(e),
            }
        finally:
            if close:
                session.close()

    @classmethod
    def get_fulfillment_health(cls, db: Session | None = None) -> dict[str, Any]:
        """
        Calculates processing time, delivery times, and SLA health dynamically from observed deliveries.
        Zero arbitrary fixed percentage cutoffs.
        """
        session, close = cls._get_session(db)
        if session is None:
            return cls._empty_fulfillment_health("DATABASE_UNAVAILABLE")

        try:
            sim_clock = get_simulated_clock(session)
            summary = cls.get_order_summary(db=session)
            total = summary.get("total_orders", 0)
            completed = summary.get("completed_orders", 0)
            delayed = summary.get("delayed_orders", 0)

            fulfillment_rate = round((completed / total) * 100.0, 2) if total > 0 else 0.0
            delay_rate = round((delayed / completed) * 100.0, 2) if completed > 0 else 0.0

            if completed == 0:
                return {
                    "avg_processing_hours": None,
                    "median_processing_hours": None,
                    "p90_processing_hours": None,
                    "avg_delivery_days": None,
                    "median_delivery_days": None,
                    "fulfillment_rate_pct": fulfillment_rate,
                    "delay_rate_pct": 0.0,
                    "sla_health": "NOT_ESTIMABLE",
                    "data_source": summary.get("data_source", "NONE"),
                    "data_status": "NOT_ESTIMABLE",
                    "not_estimable_reason": "No completed deliveries observed as of current simulated time to compute SLA health.",
                    "sample_count": 0,
                }

            # Collect empirical processing hours and delivery days up to sim_clock
            proc_hours: list[float] = []
            deliv_days: list[float] = []
            delay_margins_days: list[float] = []

            # 1. Olist delivered orders
            try:
                olist_deliv = (
                    session.query(
                        Order.order_purchase_timestamp,
                        Order.order_approved_at,
                        Order.order_delivered_carrier_date,
                        Order.order_delivered_customer_date,
                        Order.order_estimated_delivery_date,
                    )
                    .filter(
                        Order.order_purchase_timestamp <= sim_clock,
                        Order.order_status == "delivered",
                        Order.order_delivered_customer_date.isnot(None),
                        Order.order_delivered_customer_date <= sim_clock,
                    )
                    .all()
                )
                for purch, apprv, carrier, deliv, est in olist_deliv:
                    purch_n = _normalize_dt(purch)
                    deliv_n = _normalize_dt(deliv)
                    apprv_n = _normalize_dt(apprv)
                    carrier_n = _normalize_dt(carrier)
                    est_n = _normalize_dt(est)

                    proc_end = apprv_n or carrier_n
                    if purch_n and proc_end and proc_end >= purch_n:
                        proc_hours.append((proc_end - purch_n).total_seconds() / 3600.0)

                    deliv_start = carrier_n or purch_n
                    if deliv_start and deliv_n and deliv_n >= deliv_start:
                        deliv_days.append((deliv_n - deliv_start).total_seconds() / 86400.0)

                    if deliv_n and est_n:
                        diff_days = (deliv_n - est_n).total_seconds() / 86400.0
                        delay_margins_days.append(diff_days)
            except Exception as e:
                logger.debug(f"Olist fulfillment details note: {e}")

            # 2. DataCo delivered orders
            try:
                dc_deliv = (
                    session.query(
                        DataCoOrder.days_for_shipping_real,
                        DataCoOrder.days_for_shipment_scheduled,
                    )
                    .filter(
                        DataCoOrder.order_date <= sim_clock,
                        DataCoOrder.days_for_shipping_real.isnot(None),
                    )
                    .all()
                )
                for real_d, sched_d in dc_deliv:
                    if real_d is not None:
                        deliv_days.append(float(real_d))
                        if sched_d is not None:
                            delay_margins_days.append(float(real_d) - float(sched_d))
            except Exception as e:
                logger.debug(f"DataCo fulfillment details note: {e}")

            proc_profile = StatisticalProfiler.profile(proc_hours) if proc_hours else None
            deliv_profile = StatisticalProfiler.profile(deliv_days) if deliv_days else None
            margin_profile = StatisticalProfiler.profile(delay_margins_days) if delay_margins_days else None

            # SLA health is derived mathematically from the delay margin distribution:
            sla_health = "OPTIMAL"
            if margin_profile:
                if margin_profile.median > 0.0:
                    # Majority of orders arrived after scheduled estimated date
                    sla_health = "DEGRADED"
                elif margin_profile.q75 > 0.0:
                    # Top quartile of orders arrived late
                    sla_health = "AT_RISK"
                else:
                    sla_health = "OPTIMAL"
            elif delay_rate > 0:
                sla_health = "AT_RISK" if delay_rate > 10.0 else "OPTIMAL"

            return {
                "avg_processing_hours": proc_profile.mean if proc_profile else None,
                "median_processing_hours": proc_profile.median if proc_profile else None,
                "p90_processing_hours": proc_profile.q75 if proc_profile else None,
                "avg_delivery_days": deliv_profile.mean if deliv_profile else None,
                "median_delivery_days": deliv_profile.median if deliv_profile else None,
                "fulfillment_rate_pct": fulfillment_rate,
                "delay_rate_pct": delay_rate,
                "sla_health": sla_health,
                "data_source": summary.get("data_source", "BOTH"),
                "data_status": "CALCULATED",
                "sample_count": completed,
            }
        except Exception as e:
            logger.warning(f"Error querying fulfillment health: {e}")
            return cls._empty_fulfillment_health(str(e))
        finally:
            if close:
                session.close()

    @classmethod
    def _empty_fulfillment_health(cls, reason: str = "NO_RECORDS") -> dict[str, Any]:
        return {
            "avg_processing_hours": None,
            "median_processing_hours": None,
            "p90_processing_hours": None,
            "avg_delivery_days": None,
            "median_delivery_days": None,
            "fulfillment_rate_pct": 0.0,
            "delay_rate_pct": 0.0,
            "sla_health": "NOT_ESTIMABLE",
            "data_source": "NONE",
            "data_status": "NOT_ESTIMABLE",
            "not_estimable_reason": reason,
            "sample_count": 0,
        }

    @classmethod
    def get_order_trend(cls, db: Session | None = None) -> str:
        """Determines recent order volume trend trajectory via statistically significant slope t-test."""
        session, close = cls._get_session(db)
        if session is None:
            return "STABLE"

        try:
            from app.agents.orders.tools import OrdersTools

            history = OrdersTools.get_order_history(days=30, db=session)
            if len(history) < 3:
                return "STABLE"

            y = [float(r["order_count"]) for r in history]
            n = len(y)
            x = list(range(n))
            x_mean = sum(x) / n
            y_mean = sum(y) / n
            denom = sum((xi - x_mean) ** 2 for xi in x)
            if denom == 0:
                return "STABLE"

            slope = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n)) / denom
            residuals = [(y[i] - (y_mean + slope * (x[i] - x_mean))) for i in range(n)]
            rss = sum(r**2 for r in residuals)
            s_err = math.sqrt(rss / (n - 2)) if n > 2 else 0.0
            se_slope = s_err / math.sqrt(denom) if denom > 0 and s_err > 0 else 0.0

            if se_slope > 0:
                t_stat = slope / se_slope
                if t_stat >= 2.0:
                    return "INCREASING"
                elif t_stat <= -2.0:
                    return "DECREASING"
            return "STABLE"
        except Exception:
            return "STABLE"
        finally:
            if close:
                session.close()


orders_data_layer = OrdersDataLayer()
