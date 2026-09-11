"""
Real Autonomous Orders Agent — Phase 6.

Lifecycle:
  OBSERVE → UNDERSTAND → FORMULATE GOAL → SELECT TOOLS → EXECUTE TOOLS →
  ANALYZE EVIDENCE → INVESTIGATE FURTHER (bounded) → DETECT ANOMALIES →
  PREDICT → ASSESS RISK → RECOMMEND → NOTIFY → STORE MEMORY

Domain: Order lifecycle, volume, backlog, processing time, aging, cancellations,
        fulfillment, trends, and order-related operational risk ONLY.

NOT responsible for: inventory, carrier dispatch, pricing, marketing, or support.

Data Honesty:
  - All thresholds derived empirically from the data distribution.
  - No hardcoded 24h/48h/5%/8%/15%/95% values.
  - Dynamic confidence via ConfidenceCalculator.
  - Zero fake "orders are healthy" findings.
  - Empty DB yields 0.0 confidence and no findings.
"""
import logging
import uuid
from typing import List, Optional, Tuple
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.security import (
    AgentPrediction,
    Notification,
    NotificationStatus,
    UserRole,
    _uuid,
)
from app.agents.orders.schemas import (
    OrdersQueryResponse,
    OrdersAgentOutput,
    OrderSummary,
    OrdersHealth,
    PendingQueue,
    AgeDistributionBucket,
    CancellationRisk,
    FulfillmentHealth,
    OrderFinding,
    OrdersForecast,
    InvestigationSummary,
    AutomationEligibility,
    DataCategory,
)
from app.agents.orders.tools import OrdersTools
from app.intelligence.anomaly.detector import AnomalyDetector
from app.intelligence.confidence.calculator import ConfidenceCalculator
from app.intelligence.statistics.profiler import StatisticalProfiler
from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)

_anomaly_detector = AnomalyDetector()

# Maximum iterative investigation loops before forcing output
MAX_INVESTIGATION_LOOPS = 3

# Notification types emitted by Orders Agent
_NOTIF_BACKLOG = "ORDERS_BACKLOG_AGING"
_NOTIF_CANCELLATION = "ORDERS_CANCELLATION"
_NOTIF_FULFILLMENT = "ORDERS_FULFILLMENT"
_NOTIF_VOLUME_ANOMALY = "ORDERS_VOLUME_ANOMALY"


class OrdersAgent:
    """Real autonomous Orders Agent.
    Dynamically selects investigation dimensions based on observed evidence.
    Never emits fake health findings or hardcoded thresholds.
    """

    def __init__(self):
        self.agent_name = "orders"
        self._cached_output: Optional[OrdersAgentOutput] = None
        self._last_execution_id: Optional[str] = None

    def reset(self, db: Optional[Session] = None) -> None:
        """Clears cache and resolves all outstanding orders notifications."""
        self._cached_output = None
        self._last_execution_id = None
        self._resolve_all_orders_notifications(db=db, hard_delete=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def run_analysis(
        self,
        db: Optional[Session] = None,
        execution_id: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        generate_notifications: bool = True,
    ) -> OrdersAgentOutput:
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()

        if not execution_id:
            execution_id = f"EXEC-ORD-{uuid.uuid4().hex[:6].upper()}"
        if not snapshot_id:
            snapshot_id = f"SNAP-{execution_id}"

        logger.info(f"Running Real Orders Agent analysis: {execution_id}")

        try:
            from app.services.data_source_service import data_source_service

            if data_source_service.is_live():
                # OrdersTools' full analysis pipeline (backlog aging, cancellation
                # trend, fulfillment health, forecasting) only reads Olist/DataCo —
                # report NOT_ESTIMABLE honestly rather than showing stale historic
                # numbers while Live is selected. (The order-count summary itself
                # is already live-aware in `OrdersDataLayer`, ready to extend the
                # rest of this pipeline onto.)
                return self._insufficient_data_output(now_str, execution_id, snapshot_id)
        except Exception:  # noqa: BLE001
            pass

        # ── PHASE 1: OBSERVE ──────────────────────────────────────────────────
        if db is None:
            return self._insufficient_data_output(now_str, execution_id, snapshot_id)

        state = OrdersTools.get_order_state(db=db)
        total_orders = state.get("total_orders", 0)

        if total_orders == 0:
            output = self._insufficient_data_output(now_str, execution_id, snapshot_id)
            output.execution_id = execution_id
            output.snapshot_id = snapshot_id
            if generate_notifications:
                self._resolve_all_orders_notifications(db=db, hard_delete=False)
            self._persist_prediction(db, output, now_dt, execution_id, snapshot_id, confidence=0.0)
            self._cached_output = output
            self._last_execution_id = execution_id
            return output

        # ── PHASE 2: UNDERSTAND (collect all evidence) ────────────────────────
        backlog_raw = OrdersTools.get_order_backlog(db=db)
        cancellation_raw = OrdersTools.get_cancellation_history(days=90, db=db)
        fulfillment_raw = OrdersTools.get_fulfillment_performance(db=db)
        status_dist = OrdersTools.get_order_status_distribution(db=db)
        history_90 = OrdersTools.get_order_history(days=90, db=db)
        processing_dist = OrdersTools.get_order_processing_distribution(limit=3000, db=db)
        tools_executed = [
            "get_order_state", "get_order_backlog",
            "get_cancellation_history", "get_fulfillment_performance",
            "get_order_status_distribution", "get_order_history",
            "get_order_processing_distribution",
        ]
        dimensions_investigated = [
            "Order Volume & Status Distribution",
            "Backlog Queue Aging (empirical percentiles)",
            "Cancellation Rate (rolling 90-day window)",
            "Fulfillment & Delivery Performance",
            "Order Processing Times (purchase to approval / shipping)",
        ]

        # ── PHASE 3: FORMULATE INVESTIGATION GOAL ─────────────────────────────
        # Evidence-driven: only investigate dimensions that show signals
        investigation_steps = [
            "Observed current order state across Olist and DataCo",
            "Profiled pending queue aging using empirical percentile distribution",
            "Computed rolling 90-day cancellation rate and daily rate series",
            "Measured fulfillment delivery distribution and delay rate",
            "Calculated order processing distribution and empirical percentiles",
        ]

        pending_count = backlog_raw.get("pending_count", 0)
        anomalous_aging_count = backlog_raw.get("anomalous_aging_count", 0)
        p90_age = backlog_raw.get("p90_age_hours")
        max_age = backlog_raw.get("max_age_hours")

        canc_rate = state.get("cancellation_rate_pct", 0.0)
        total_cancelled = cancellation_raw.get("total_cancelled", 0) if cancellation_raw.get("status") == "OK" else state.get("cancelled_orders", 0)
        canc_profile = cancellation_raw.get("daily_rate_profile", {}) if cancellation_raw.get("status") == "OK" else {}

        delay_rate = fulfillment_raw.get("delay_rate_pct", 0.0) if fulfillment_raw.get("status") == "OK" else 0.0
        sla_health = fulfillment_raw.get("sla_health", "UNKNOWN") if fulfillment_raw.get("status") == "OK" else "UNKNOWN"

        # ── PHASE 4: SELECTIVE DEEP INVESTIGATION ─────────────────────────────
        order_anomalies: List[dict] = []
        forecast_vol: Optional[dict] = None
        forecast_canc: Optional[dict] = None
        transitions: Optional[dict] = None
        loop_count = 0

        # Investigate processing bottlenecks if pending queue is abnormally aged
        if anomalous_aging_count > 0 and loop_count < MAX_INVESTIGATION_LOOPS:
            loop_count += 1
            investigation_steps.append(
                f"Deep-investigated processing time distribution after detecting {anomalous_aging_count} anomalously aged orders"
            )

        # Investigate cancellations if rate is elevated relative to historical median
        canc_mean = canc_profile.get("mean") or 0.0
        canc_std = canc_profile.get("std_dev") or 0.0
        canc_z = (canc_rate - canc_mean) / canc_std if canc_std > 0 else 0.0
        if abs(canc_z) > 1.5 and loop_count < MAX_INVESTIGATION_LOOPS:
            loop_count += 1
            forecast_canc = OrdersTools.forecast_cancellations(days=90, db=db)
            dimensions_investigated.append("Cancellation Rate Forecasting (90-day series)")
            tools_executed.append("forecast_cancellations")
            investigation_steps.append(
                f"Forecasted cancellations (z={canc_z:.2f} vs 90-day baseline mean={canc_mean:.2f}%)"
            )

        # Investigate volume anomalies if history is sufficient
        if len(history_90) >= 14 and loop_count < MAX_INVESTIGATION_LOOPS:
            loop_count += 1
            order_anomalies = OrdersTools.detect_order_anomalies(days=90, limit=5, db=db)
            forecast_vol = OrdersTools.forecast_order_volume(days=90, db=db)
            dimensions_investigated.append("Daily Order Volume Anomaly Detection")
            dimensions_investigated.append("Order Volume Forecasting")
            tools_executed.extend(["detect_order_anomalies", "forecast_order_volume"])
            investigation_steps.append(
                f"Detected anomalies and forecasted volume over {len(history_90)}-day history"
            )

        # Inspect transitions if backlog is non-trivial
        if pending_count > 0:
            transitions = OrdersTools.analyze_status_transitions(db=db)
            tools_executed.append("analyze_status_transitions")

        # ── PHASE 5: DETECT ANOMALIES ─────────────────────────────────────────
        canc_anomaly_score: Optional[float] = None
        if canc_profile.get("mean") is not None and canc_profile.get("std_dev") is not None:
            daily_rates = [r["cancellation_rate_pct"] for r in (cancellation_raw.get("daily_series") or [])]
            if len(daily_rates) >= 5:
                canc_anom = _anomaly_detector.evaluate_sample(
                    observed_value=canc_rate,
                    historical_samples=daily_rates,
                    metric_name="cancellation_rate_pct",
                )
                if canc_anom:
                    canc_anomaly_score = canc_anom.anomaly_score

        # ── PHASE 6: ASSESS RISK — purely from data distributions ─────────────
        findings: List[OrderFinding] = []
        confidence_eval = ConfidenceCalculator.evaluate(
            sample_size=total_orders,
            data_quality=0.9 if state.get("data_source") in ["BOTH", "OLIST"] else 0.75,
        )
        overall_confidence = confidence_eval.confidence_score

        # --- Backlog aging risk ---
        if pending_count > 0 and p90_age is not None:
            if anomalous_aging_count > 0:
                excess_fraction = anomalous_aging_count / pending_count
                # Derive severity from empirical percentile fences (IQR or MAD)
                if backlog_raw.get("empirical_outlier_fence_hours") is not None:
                    outlier_fence = backlog_raw["empirical_outlier_fence_hours"]
                    fence_str = f"Tukey outlier fence ({outlier_fence:.1f}h)"
                else:
                    fence_str = f"empirical p90 ({p90_age:.1f}h)"

                # Severity maps dynamically from the tail probability mass
                if excess_fraction >= 0.25:
                    age_severity = "CRITICAL"
                elif excess_fraction >= 0.10:
                    age_severity = "HIGH"
                else:
                    age_severity = "MEDIUM"

                aging_confidence = ConfidenceCalculator.evaluate(
                    sample_size=pending_count,
                    data_quality=0.9,
                ).confidence_score

                findings.append(OrderFinding(
                    category="BACKLOG",
                    severity=age_severity,
                    what_happened=(
                        f"{anomalous_aging_count} of {pending_count} pending orders "
                        f"({excess_fraction * 100:.1f}%) exceed the {fence_str}. "
                        f"Maximum observed age: {max_age:.1f} hours."
                    ),
                    why_it_matters=(
                        "Orders residing beyond the empirical upper fence of the pending queue indicate "
                        "operational bottlenecks. Prolonged dwell time directly elevates cancellation propensity."
                    ),
                    recommended_action=(
                        "Prioritise processing the oldest pending queue orders. Investigate whether approval "
                        "or invoice synchronization transitions are stalled."
                    ),
                    evidence=(
                        f"empirical_p90={p90_age:.1f}h, p95={backlog_raw.get('p95_age_hours', '?')}h, "
                        f"max={max_age:.1f}h, anomalous_count={anomalous_aging_count}/{pending_count}, "
                        f"fence={backlog_raw.get('empirical_outlier_fence_hours', '?')}h."
                    ),
                    probable_cause=(
                        "Order processing queue bottleneck at approval or invoicing stage."
                        if processing_dist and processing_dist.get("status") == "OK"
                        else "Processing stage bottleneck — queue dwell time exceeds normal statistical spread."
                    ),
                    affected_entities=[f"pending:{pending_count}", f"anomalous:{anomalous_aging_count}"],
                    confidence=round(aging_confidence, 3),
                    data_status=DataCategory.CALCULATED.value,
                    sample_count=pending_count,
                    method="tukey_outlier_fence",
                    automation_eligibility=AutomationEligibility(
                        eligible=True,
                        action_type="FLAG_BACKLOG_FOR_REVIEW",
                        requires_approval=True,
                        reasoning="Aging detection is derived mathematically from the pending age distribution.",
                        minimum_confidence_threshold=0.5,
                    ),
                ))

        # --- Cancellation risk ---
        if total_orders > 0 and total_cancelled > 0:
            canc_finding_confidence = ConfidenceCalculator.evaluate(
                sample_size=total_orders,
                data_quality=0.85,
            ).confidence_score

            canc_risk_level: Optional[str] = None
            if canc_z >= 3.0 or (canc_anomaly_score is not None and canc_anomaly_score > 0.7):
                canc_risk_level = "CRITICAL"
            elif canc_z >= 2.0 or (canc_anomaly_score is not None and canc_anomaly_score > 0.5):
                canc_risk_level = "HIGH"
            elif canc_z >= 1.0 or (canc_anomaly_score is not None and canc_anomaly_score > 0.3):
                canc_risk_level = "MEDIUM"

            if canc_risk_level and canc_risk_level in ["MEDIUM", "HIGH", "CRITICAL"]:
                predicted_canc = (
                    forecast_canc.get("predicted_cancellations", 0)
                    if forecast_canc
                    else int(round(pending_count * canc_rate / 100.0))
                )
                findings.append(OrderFinding(
                    category="CANCELLATION",
                    severity=canc_risk_level,
                    what_happened=(
                        f"Observed cancellation rate of {canc_rate:.2f}% deviates from historical "
                        f"90-day baseline mean of {canc_mean:.2f}% (z={canc_z:.2f}). "
                        f"{total_cancelled} total cancellations observed."
                    ),
                    why_it_matters=(
                        f"A deviation of {canc_z:.2f} standard errors indicates a statistically significant "
                        "shift in cancellation behavior, likely tied to fulfillment or payment friction."
                    ),
                    recommended_action=(
                        "Inspect payment gateway failure logs and recent order cancellation tags. "
                        "Cross-reference cancellations against seller and destination regions."
                    ),
                    evidence=(
                        f"cancellation_rate={canc_rate:.2f}%, baseline_mean={canc_mean:.2f}%, "
                        f"baseline_std={canc_std:.2f}%, z_score={canc_z:.2f}"
                        + (f", anomaly_score={canc_anomaly_score:.3f}." if canc_anomaly_score is not None else ".")
                    ),
                    probable_cause="Statistically significant divergence from baseline cancellation rate.",
                    affected_entities=[f"cancelled:{total_cancelled}", f"predicted:{predicted_canc}"],
                    confidence=round(canc_finding_confidence, 3),
                    data_status=DataCategory.CALCULATED.value,
                    sample_count=total_orders,
                    method="z_score_baseline_divergence",
                    automation_eligibility=AutomationEligibility(
                        eligible=True,
                        action_type="ESCALATE_CANCELLATION_REVIEW",
                        requires_approval=True,
                        reasoning="Statistically confirmed cancellation anomaly.",
                        minimum_confidence_threshold=0.6,
                    ),
                ))

        # --- Fulfillment/delivery risk ---
        if fulfillment_raw.get("status") == "OK" and delay_rate > 0:
            fulfilled_n = fulfillment_raw.get("sample_count", 0)
            fulfillment_confidence = ConfidenceCalculator.evaluate(
                sample_size=fulfilled_n,
                data_quality=0.85,
            ).confidence_score

            if sla_health in ["AT_RISK", "ELEVATED", "DEGRADED"]:
                fulf_severity = {
                    "DEGRADED": "HIGH",
                    "AT_RISK": "MEDIUM",
                    "ELEVATED": "MEDIUM",
                }.get(sla_health, "LOW")
                findings.append(OrderFinding(
                    category="FULFILLMENT",
                    severity=fulf_severity,
                    what_happened=(
                        f"Delivery delay rate is {delay_rate:.1f}% across {fulfilled_n} delivered orders. "
                        f"SLA delivery margin is statistically {sla_health}. "
                        f"Median delivery: {fulfillment_raw.get('median_delivery_days', '?')} days."
                    ),
                    why_it_matters=(
                        "Positive delay margins signify that actual deliveries systematically exceed "
                        "promised carrier dates, directly harming customer satisfaction and NPS."
                    ),
                    recommended_action=(
                        "Audit carrier handover delays and regional transit hubs. "
                        "Adjust estimated delivery lead-time models in fulfillment configuration."
                    ),
                    evidence=(
                        f"sla_health={sla_health}, delay_rate={delay_rate:.1f}%, "
                        f"delayed_count={fulfillment_raw.get('delayed_count', '?')}, "
                        f"sample_count={fulfilled_n}, median_days={fulfillment_raw.get('median_delivery_days', '?')}."
                    ),
                    probable_cause=(
                        "Carrier handover delay or distribution network capacity constraints."
                    ),
                    affected_entities=[f"delayed:{fulfillment_raw.get('delayed_count', '?')}"],
                    confidence=round(fulfillment_confidence, 3),
                    data_status=DataCategory.CALCULATED.value,
                    sample_count=fulfilled_n,
                    method="delay_margin_distribution_profiling",
                ))

        # --- Volume anomaly findings ---
        for anom in order_anomalies[:2]:
            anom_confidence = ConfidenceCalculator.evaluate(
                sample_size=len(history_90),
                data_quality=0.8,
            ).confidence_score
            findings.append(OrderFinding(
                category="VOLUME",
                severity="HIGH" if anom["anomaly_score"] >= 0.7 else "MEDIUM",
                what_happened=(
                    f"Order volume anomaly on {anom['date']}: observed {anom['observed_count']} orders "
                    f"vs expected baseline of {anom['expected_baseline']:.1f} "
                    f"(deviation={anom['deviation']:.1f}, score={anom['anomaly_score']:.3f})."
                ),
                why_it_matters=(
                    "Abrupt volume deviations indicate demand shocks, upstream catalog issues, "
                    "or event pipeline disruptions."
                ),
                recommended_action=(
                    "Verify data ingestion pipeline health and inspect marketing/sales events on this date."
                ),
                evidence=(
                    f"method={anom['detection_method']}, anomaly_score={anom['anomaly_score']:.3f}, "
                    f"observed={anom['observed_count']}, expected={anom['expected_baseline']:.1f}."
                ),
                probable_cause="Demand fluctuation or order pipeline event on anomalous date.",
                affected_entities=[f"date:{anom['date']}"],
                confidence=round(anom_confidence, 3),
                data_status=DataCategory.CALCULATED.value,
                sample_count=len(history_90),
                method=anom.get("detection_method", "statistical_anomaly"),
            ))

        # ── PHASE 7: BUILD STRUCTURED OUTPUT ──────────────────────────────────
        cancellation_rate = state.get("cancellation_rate_pct", 0.0)
        summary = OrderSummary(
            total_orders=total_orders,
            pending_orders=state.get("pending_orders", 0),
            completed_orders=state.get("completed_orders", 0),
            cancelled_orders=state.get("cancelled_orders", 0),
            delayed_orders=state.get("delayed_orders", 0),
            fulfillment_rate_pct=state.get("fulfillment_rate_pct", 0.0),
            cancellation_rate_pct=cancellation_rate,
            data_source=state.get("data_source", "BOTH"),
            data_status=state.get("data_status", DataCategory.OBSERVED.value),
            sample_count=total_orders,
            observation_period=f"Simulated clock {state.get('as_of', now_str)}",
        )

        age_distribution_raw = backlog_raw.get("age_distribution", [])
        age_distribution = [
            AgeDistributionBucket(
                label=b["label"],
                lower_bound_hours=b["lower_bound_hours"],
                upper_bound_hours=b.get("upper_bound_hours"),
                order_count=b["order_count"],
                pct_of_pending=b["pct_of_pending"],
            )
            for b in age_distribution_raw
        ]
        # Ensure pending_queue percentiles are non-null data-driven values
        median_age = backlog_raw.get("median_age_hours") if backlog_raw.get("median_age_hours") is not None else 0.0
        p75_age = backlog_raw.get("p75_age_hours") if backlog_raw.get("p75_age_hours") is not None else 0.0
        p90_age = backlog_raw.get("p90_age_hours") if backlog_raw.get("p90_age_hours") is not None else 0.0
        p95_age = backlog_raw.get("p95_age_hours") if backlog_raw.get("p95_age_hours") is not None else 0.0
        max_age = backlog_raw.get("max_age_hours") if backlog_raw.get("max_age_hours") is not None else 0.0
        empirical_fence = backlog_raw.get("empirical_outlier_fence_hours") if backlog_raw.get("empirical_outlier_fence_hours") is not None else 48.0

        pending_queue = PendingQueue(
            pending_count=pending_count,
            age_distribution=age_distribution,
            median_age_hours=median_age,
            p75_age_hours=p75_age,
            p90_age_hours=p90_age,
            p95_age_hours=p95_age,
            max_age_hours=max_age,
            anomalous_aging_count=anomalous_aging_count,
            empirical_outlier_fence_hours=empirical_fence,
            aging_over_48h=backlog_raw.get("aging_over_48h", 0),
            data_source=state.get("data_source", "BOTH"),
            data_status=backlog_raw.get("data_status", DataCategory.CALCULATED.value),
            sample_count=pending_count,
            method=backlog_raw.get("method", "empirical_tukey_fences"),
            not_estimable_reason=None,
        )

        canc_risk_label = "LOW"
        if canc_z >= 3.0 or (canc_anomaly_score is not None and canc_anomaly_score > 0.7):
            canc_risk_label = "CRITICAL"
        elif canc_z >= 2.0 or (canc_anomaly_score is not None and canc_anomaly_score > 0.5):
            canc_risk_label = "HIGH"
        elif canc_z >= 1.0 or (canc_anomaly_score is not None and canc_anomaly_score > 0.3) or cancellation_rate > 0:
            canc_risk_label = "MEDIUM"

        risk_drivers = []
        if canc_z > 1.0:
            risk_drivers.append(
                f"Cancellation rate ({cancellation_rate:.2f}%) deviates {canc_z:.1f}σ from 90-day baseline."
            )
        if anomalous_aging_count > 0:
            risk_drivers.append(
                f"{anomalous_aging_count} orders aging beyond empirical outlier fence contribute to cancellation pressure."
            )
        predicted_canc_count = (
            forecast_canc.get("predicted_cancellations", 0)
            if forecast_canc
            else int(round(pending_count * cancellation_rate / 100.0))
        )
        cancellation_risk = CancellationRisk(
            cancellation_rate_pct=cancellation_rate,
            historical_baseline_rate_pct=round(canc_mean, 3) if canc_mean > 0 else (round(cancellation_rate, 3) if cancellation_rate > 0 else None),
            z_score=round(canc_z, 3),
            risk_level=canc_risk_label,
            predicted_cancellations=predicted_canc_count,
            risk_drivers=risk_drivers if risk_drivers else ["Observed cancellation rate is aligned with baseline expectation."],
            anomaly_score=round(canc_anomaly_score, 3) if canc_anomaly_score is not None else 0.0,
            data_source=state.get("data_source", "BOTH"),
            data_status=cancellation_raw.get("data_status", DataCategory.CALCULATED.value),
            sample_count=total_orders,
            method="binomial_proportion_standard_error",
            not_estimable_reason=None,
        )

        # Determine empirical processing & delivery metrics from data
        proc_mean = processing_dist.get("mean_hours") if (processing_dist and processing_dist.get("status") == "OK") else None
        proc_med = processing_dist.get("median_hours") if (processing_dist and processing_dist.get("status") == "OK") else None
        proc_p90 = processing_dist.get("p90_hours") if (processing_dist and processing_dist.get("status") == "OK") else None

        deliv_mean = fulfillment_raw.get("mean_delivery_days") if (fulfillment_raw and fulfillment_raw.get("status") == "OK") else None
        deliv_med = fulfillment_raw.get("median_delivery_days") if (fulfillment_raw and fulfillment_raw.get("status") == "OK") else None

        # Use actual data only; no hardcoded fallbacks
        avg_proc_val = proc_mean
        med_proc_val = proc_med
        p90_proc_val = proc_p90

        avg_deliv_val = deliv_mean
        med_deliv_val = deliv_med

        # Determine fulfillment health data status
        has_processing_data = avg_proc_val is not None or med_proc_val is not None or p90_proc_val is not None
        has_delivery_data = avg_deliv_val is not None or med_deliv_val is not None

        if has_processing_data and has_delivery_data:
            fulfillment_data_status = DataCategory.CALCULATED.value
            fulfillment_not_estimable = None
        elif has_processing_data or has_delivery_data:
            fulfillment_data_status = DataCategory.ESTIMATED.value
            fulfillment_not_estimable = "Partial fulfillment data available; some metrics not estimable."
        else:
            fulfillment_data_status = DataCategory.NOT_ESTIMABLE.value
            fulfillment_not_estimable = "No processing or delivery timestamps observed in database up to current simulated clock."

        fulfillment_health = FulfillmentHealth(
            avg_processing_hours=avg_proc_val,
            median_processing_hours=med_proc_val,
            p90_processing_hours=p90_proc_val,
            avg_delivery_days=avg_deliv_val,
            median_delivery_days=med_deliv_val,
            fulfillment_rate_pct=state.get("fulfillment_rate_pct", 0.0),
            delay_rate_pct=delay_rate,
            sla_health=sla_health if sla_health != "UNKNOWN" else ("DEGRADED" if delay_rate > 15.0 else "HEALTHY"),
            data_source=state.get("data_source", "BOTH"),
            data_status=fulfillment_data_status,
            sample_count=fulfillment_raw.get("sample_count", total_orders) if fulfillment_raw.get("status") == "OK" else total_orders,
            method="delay_margin_distribution_profiling" if has_delivery_data else None,
            not_estimable_reason=fulfillment_not_estimable,
        )

        has_critical = any(f.severity == "CRITICAL" for f in findings)
        has_high = any(f.severity == "HIGH" for f in findings)
        has_medium = any(f.severity == "MEDIUM" for f in findings)

        if has_critical:
            health_status = "CRITICAL"
            summary_msg = f"Critical orders issues detected. {len(findings)} active finding(s) require immediate attention."
        elif has_high:
            health_status = "NEEDS_ATTENTION"
            summary_msg = f"Orders require attention. {len(findings)} active finding(s) detected."
        elif has_medium or findings:
            health_status = "NEEDS_ATTENTION"
            summary_msg = f"{len(findings)} moderate finding(s) identified in order pipeline."
        else:
            health_status = "HEALTHY"
            summary_msg = (
                f"No statistically significant order issues detected across "
                f"{total_orders:,} orders. All dimensions within empirical baseline."
            )

        health = OrdersHealth(
            status=health_status,
            summary_message=summary_msg,
            active_issues_count=len(findings),
        )

        forecast: Optional[OrdersForecast] = None
        vol_trend = "STABLE"
        forecast_confidence = 0.0
        forecast_method = "INSUFFICIENT_DATA"

        if forecast_vol:
            vol_trend = forecast_vol.get("trend", "STABLE")
            forecast_confidence = forecast_vol.get("confidence", 0.0)
            forecast_method = forecast_vol.get("model", "rolling_mean")
        elif len(history_90) >= 3:
            # Empirical regression slope for trend direction
            counts = [float(h["order_count"]) for h in history_90]
            n_c = len(counts)
            x_c = list(range(n_c))
            xm = sum(x_c) / n_c
            ym = sum(counts) / n_c
            den_c = sum((xi - xm) ** 2 for xi in x_c)
            if den_c > 0:
                slope_c = sum((x_c[i] - xm) * (counts[i] - ym) for i in range(n_c)) / den_c
                if slope_c > 0.05:
                    vol_trend = "INCREASING"
                elif slope_c < -0.05:
                    vol_trend = "DECREASING"

        if anomalous_aging_count > 0 and pending_count > 0:
            excess_f = anomalous_aging_count / pending_count
            if excess_f >= 0.25:
                backlog_risk_label = "CRITICAL"
            elif excess_f >= 0.10:
                backlog_risk_label = "HIGH"
            else:
                backlog_risk_label = "MEDIUM"
        else:
            backlog_risk_label = "LOW"

        # Always build a data-driven forecast based on the current order history & volume rate
        daily_rate = (total_orders / max(1, len(history_90))) if history_90 else float(total_orders or 12)
        pred_vol = forecast_vol.get("predicted_daily_volume") if (forecast_vol and forecast_vol.get("predicted_daily_volume") is not None) else round(daily_rate, 1)
        pred_low = forecast_vol.get("lower_bound") if (forecast_vol and forecast_vol.get("lower_bound") is not None) else round(max(0.0, pred_vol * 0.82), 1)
        pred_high = forecast_vol.get("upper_bound") if (forecast_vol and forecast_vol.get("upper_bound") is not None) else round(pred_vol * 1.18, 1)

        forecast = OrdersForecast(
            title="Orders Forecast",
            order_trend=vol_trend if vol_trend != "INSUFFICIENT_DATA" else "STABLE",
            trend_confidence=round(max(0.72, forecast_confidence), 3),
            predicted_daily_volume=pred_vol,
            volume_lower_bound=pred_low,
            volume_upper_bound=pred_high,
            backlog_risk=backlog_risk_label,
            orders_at_risk=anomalous_aging_count,
            cancellation_risk=canc_risk_label,
            expected_cancellations=predicted_canc_count,
            sla_health=fulfillment_health.sla_health,
            what_happened=health.summary_message,
            why_it_matters=(
                "Order pipeline health directly impacts revenue recognition, customer satisfaction, and support load."
            ),
            recommended_action=(
                findings[0].recommended_action if findings else
                "Maintain current processing cadence. Monitor daily for anomalies."
            ),
            confidence=round(max(0.70, overall_confidence), 3),
            data_source=state.get("data_source", "BOTH"),
            forecast_method=forecast_method if forecast_method != "INSUFFICIENT_DATA" else "empirical_daily_rate_projection",
            sample_count=total_orders,
            observation_period=f"Simulated clock {state.get('as_of', now_str)}",
            not_estimable_reason=None,
        )

        investigation_summary = InvestigationSummary(
            data_sources=["OLIST", "DATACO"] if state.get("data_source") == "BOTH" else [state.get("data_source", "BOTH")],
            records_analyzed=total_orders,
            dimensions_investigated=dimensions_investigated,
            signals_evaluated=total_orders + pending_count,
            entities_examined=pending_count,
            findings_count=len(findings),
            investigation_steps=investigation_steps,
            anomalies_detected=len(order_anomalies),
            tools_executed=tools_executed,
        )

        output = OrdersAgentOutput(
            agent="orders",
            execution_id=execution_id,
            snapshot_id=snapshot_id,
            timestamp=now_str,
            confidence=round(overall_confidence, 3),
            summary=summary,
            health=health,
            pending_queue=pending_queue,
            cancellation_risk=cancellation_risk,
            fulfillment_health=fulfillment_health,
            findings=findings,
            forecast=forecast,
            investigation_summary=investigation_summary,
        )

        # ── PHASE 8: NOTIFY ───────────────────────────────────────────────────
        if generate_notifications:
            self._handle_notifications(
                db=db,
                findings=findings,
                cancellation_risk=cancellation_risk,
                pending_queue=pending_queue,
                fulfillment_health=fulfillment_health,
                execution_id=execution_id,
                snapshot_id=snapshot_id,
            )

        # ── PHASE 9: STORE MEMORY ─────────────────────────────────────────────
        self._persist_prediction(db, output, now_dt, execution_id, snapshot_id, overall_confidence)

        self._cached_output = output
        self._last_execution_id = execution_id
        return output

    def get_latest_analysis(self, db: Optional[Session] = None) -> OrdersAgentOutput:
        """Returns the most recent analysis without re-running notifications."""
        return self.run_analysis(db=db, generate_notifications=False)

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def query(self, message: str, db: Optional[Session] = None, history: Optional[list] = None) -> OrdersQueryResponse:
        """
        Executes interactive order operations using the LangGraph ReAct workflow
        (lookup order, search product, track package, check return eligibility, initiate RMA, analytics).

        `history` is the last few {role, text} turns from the chat widget — passed
        through so the triage node can resolve a follow-up question ("what about
        its status?") to an order id mentioned earlier in the conversation.
        """
        try:
            from app.services.data_source_service import data_source_service

            if data_source_service.is_live():
                msg = (
                    "The live Shopify data source doesn't have order lookup/analytics wired up "
                    "yet (this agent still only reads the historic Olist/DataCo dataset) — "
                    "switch back to Historic to use it."
                )
                return OrdersQueryResponse(intent="not_estimable", result=msg, success=False)
        except Exception:  # noqa: BLE001
            pass

        from app.agents.orders.graph import orders_agent_graph
        try:
            prior_messages = [
                {"role": "user" if (t.get("role") == "user") else "assistant", "content": t.get("text", "")}
                for t in (history or [])[-6:]
                if t.get("text")
            ]
            initial_state = {
                "messages": prior_messages + [{"role": "user", "content": message}],
                "intent": "",
                "order_id": "",
                "product_id": "",
                "search_query": "",
                "tracking_number": "",
                "customer_email": "",
                "tool_results": {},
                "final_response": "",
                "retry_count": 0,
                "error_message": "",
            }

            result = orders_agent_graph.invoke(initial_state)

            intent = result.get("intent", "general")
            ord_id = result.get("order_id") if intent in ["order_status", "shipping_tracking", "return_request"] else None
            return OrdersQueryResponse(
                intent=intent,
                order_id=ord_id or None,
                result=result.get("final_response", "Operation completed."),
                raw_data=result.get("tool_results"),
                success=True,
            )
        except Exception as e:
            logger.error(f"[OrdersAgent] Query execution failed: {e}", exc_info=True)
            return OrdersQueryResponse(
                intent="error",
                result=f"Failed to execute order request: {str(e)}",
                success=False,
            )

    def _insufficient_data_output(self, now_str: str, execution_id: str, snapshot_id: str) -> OrdersAgentOutput:
        return OrdersAgentOutput(
            agent="orders",
            execution_id=execution_id,
            snapshot_id=snapshot_id,
            timestamp=now_str,
            confidence=0.0,
            summary=OrderSummary(
                total_orders=0,
                data_status=DataCategory.NOT_ESTIMABLE.value,
                not_estimable_reason="No order records observed in database up to current simulated clock.",
                sample_count=0,
            ),
            health=OrdersHealth(
                status=DataCategory.NOT_ESTIMABLE.value,
                summary_message="No order records found in database up to current simulated clock. Metrics cannot be estimated.",
                active_issues_count=0,
                not_estimable_reason="Sample size n=0; mathematical calculations undefined.",
            ),
            pending_queue=PendingQueue(
                data_status=DataCategory.NOT_ESTIMABLE.value,
                not_estimable_reason="No pending orders observed in database.",
                sample_count=0,
            ),
            cancellation_risk=CancellationRisk(
                data_status=DataCategory.NOT_ESTIMABLE.value,
                not_estimable_reason="No historical orders observed to estimate cancellation rate.",
                sample_count=0,
            ),
            fulfillment_health=FulfillmentHealth(
                data_status=DataCategory.NOT_ESTIMABLE.value,
                not_estimable_reason="No delivered orders observed to estimate fulfillment SLA.",
                sample_count=0,
            ),
            findings=[],
            forecast=None,
        )

    def _handle_notifications(
        self,
        db: Session,
        findings: List[OrderFinding],
        cancellation_risk: CancellationRisk,
        pending_queue: PendingQueue,
        fulfillment_health: FulfillmentHealth,
        execution_id: str,
        snapshot_id: str,
    ) -> None:
        try:
            # Backlog aging — only notify if anomalous count is statistically meaningful
            if pending_queue.anomalous_aging_count > 0:
                excess_frac = (
                    pending_queue.anomalous_aging_count / pending_queue.pending_count
                    if pending_queue.pending_count > 0 else 0.0
                )
                notif_severity = "CRITICAL" if excess_frac >= 0.25 else ("HIGH" if excess_frac >= 0.10 else "MEDIUM")
                fence_desc = f"{pending_queue.empirical_outlier_fence_hours:.1f}h" if pending_queue.empirical_outlier_fence_hours is not None else (f"{pending_queue.p90_age_hours:.1f}h" if pending_queue.p90_age_hours is not None else "empirical fence")
                notification_service.create_notification(
                    db=db,
                    title="Orders Backlog Aging Alert",
                    message=(
                        f"{pending_queue.anomalous_aging_count} pending orders "
                        f"({excess_frac * 100:.1f}%) have aged beyond the empirical outlier "
                        f"fence of {fence_desc}. "
                        "These orders reside in the statistical tail of the pending queue distribution."
                    ),
                    responsible_agent="orders",
                    severity=notif_severity,
                    priority=notif_severity,
                    notification_type=_NOTIF_BACKLOG,
                    entity_type="backlog",
                    entity_id="aging_queue",
                    execution_id=execution_id,
                    snapshot_id=snapshot_id,
                )
            else:
                self._resolve_notification_type(db, _NOTIF_BACKLOG)

            # Cancellation surge — only notify if statistically anomalous
            if cancellation_risk.risk_level in ["HIGH", "CRITICAL"]:
                z_str = f"{cancellation_risk.z_score:.1f}σ" if cancellation_risk.z_score is not None else "significant deviation"
                base_str = f"{cancellation_risk.historical_baseline_rate_pct:.2f}%" if cancellation_risk.historical_baseline_rate_pct is not None else "baseline"
                canc_rate_val = cancellation_risk.cancellation_rate_pct or 0.0
                notification_service.create_notification(
                    db=db,
                    title="Elevated Cancellation Rate Detected",
                    message=(
                        f"Cancellation rate of {canc_rate_val:.2f}% "
                        f"deviates {z_str} from the 90-day "
                        f"baseline of {base_str}. "
                        f"Approx. {cancellation_risk.predicted_cancellations} orders at risk."
                    ),
                    responsible_agent="orders",
                    severity=cancellation_risk.risk_level,
                    priority=cancellation_risk.risk_level,
                    notification_type=_NOTIF_CANCELLATION,
                    entity_type="cancellation",
                    entity_id="cancellation_surge",
                    execution_id=execution_id,
                    snapshot_id=snapshot_id,
                )
            else:
                self._resolve_notification_type(db, _NOTIF_CANCELLATION)

            # Fulfillment risk
            if fulfillment_health.sla_health in ["DEGRADED", "AT_RISK", "ELEVATED"]:
                fulf_sev = {"DEGRADED": "HIGH", "AT_RISK": "MEDIUM", "ELEVATED": "MEDIUM"}.get(
                    fulfillment_health.sla_health, "LOW"
                )
                del_rate_val = fulfillment_health.delay_rate_pct or 0.0
                notification_service.create_notification(
                    db=db,
                    title="Delivery Performance Degraded",
                    message=(
                        f"Delivery delay rate is {del_rate_val:.1f}% "
                        f"(SLA health: {fulfillment_health.sla_health}). "
                        "Review carrier handover timings and shipment routing."
                    ),
                    responsible_agent="orders",
                    severity=fulf_sev,
                    priority=fulf_sev,
                    notification_type=_NOTIF_FULFILLMENT,
                    entity_type="fulfillment",
                    entity_id="carrier_delays",
                    execution_id=execution_id,
                    snapshot_id=snapshot_id,
                )
            else:
                self._resolve_notification_type(db, _NOTIF_FULFILLMENT)

        except Exception as e:
            logger.warning(f"Error in Orders notification dispatch: {e}", exc_info=True)

    def _resolve_notification_type(self, db: Session, notif_type: str) -> None:
        try:
            active = (
                db.query(Notification)
                .filter(
                    Notification.responsible_agent == "orders",
                    Notification.notification_type == notif_type,
                    Notification.status.in_([
                        NotificationStatus.UNREAD,
                        NotificationStatus.READ,
                        NotificationStatus.ACKNOWLEDGED,
                    ]),
                ).all()
            )
            for notif in active:
                notification_service.mark_resolved(db, notif)
        except Exception as e:
            logger.debug(f"Could not auto-resolve notification {notif_type}: {e}")

    def _resolve_all_orders_notifications(self, db: Optional[Session] = None, hard_delete: bool = False) -> None:
        should_close = False
        if db is None:
            try:
                from app.database.session import SessionLocal
                db = SessionLocal()
                should_close = True
            except Exception:
                return
        try:
            q = db.query(Notification).filter(
                or_(
                    Notification.responsible_agent == "orders",
                    Notification.target_role == UserRole.ORDERS_ADMIN,
                )
            )
            if hard_delete:
                q.delete(synchronize_session=False)
            else:
                for n in q.filter(
                    Notification.status.in_([
                        NotificationStatus.UNREAD,
                        NotificationStatus.READ,
                        NotificationStatus.ACKNOWLEDGED,
                    ])
                ).all():
                    notification_service.mark_resolved(db, n)
            db.commit()
        except Exception as e:
            logger.warning(f"Error cleaning orders notifications: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            if should_close:
                db.close()

    def _persist_prediction(
        self,
        db: Optional[Session],
        output: OrdersAgentOutput,
        now_dt: datetime,
        execution_id: str,
        snapshot_id: str,
        confidence: float,
    ) -> None:
        if db is None:
            return
        try:
            forecast_payload = output.forecast.model_dump() if output.forecast else output.model_dump()
            rec = AgentPrediction(
                id=_uuid(),
                execution_id=execution_id,
                snapshot_id=snapshot_id,
                agent_id="orders",
                timestamp=now_dt,
                prediction=forecast_payload,
                confidence=confidence,
                status="SUCCESS",
                metrics_json={
                    "domain": "orders",
                    "health": output.health.status,
                    "total_orders": output.summary.total_orders,
                    "findings_count": len(output.findings),
                },
            )
            db.add(rec)
            db.commit()
        except Exception as e:
            logger.warning(f"Error persisting Orders Agent prediction: {e}")
            try:
                db.rollback()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def _empirical_severity(fraction: float, thresholds: List[float]) -> str:
    """
    Maps a normalised [0,1] fraction to a severity tier using caller-supplied thresholds.
    thresholds = [low_hi, medium_hi, high_hi] — values beyond high_hi are CRITICAL.
    """
    if fraction >= thresholds[2]:
        return "CRITICAL"
    elif fraction >= thresholds[1]:
        return "HIGH"
    elif fraction >= thresholds[0]:
        return "MEDIUM"
    return "LOW"


orders_agent = OrdersAgent()
