"""
Orders Agent Schemas — Adapted from m-peker/ecommerce-ai-agent.
Provides data models for both the LangGraph ReAct conversation pipeline
and empirical operational intelligence reporting.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class DataCategory(str, Enum):
    OBSERVED = "OBSERVED"  # Direct database read
    CALCULATED = "CALCULATED"  # Derived from observed values
    ESTIMATED = "ESTIMATED"  # Statistical inference
    MODELLED = "MODELLED"  # Algorithmic or regression model output
    UNAVAILABLE = "UNAVAILABLE"  # Structurally absent from schema
    NOT_ESTIMABLE = "NOT_ESTIMABLE"  # Cannot be computed mathematically from available records


class MetricProvenance(BaseModel):
    value: float | None = None
    method: str
    sample_count: int = 0
    observation_period: str | None = None
    data_status: str = DataCategory.CALCULATED.value
    not_estimable_reason: str | None = None


# ── Interactive Orders Schemas (ecommerce-ai-agent) ─────────────


class OrderItemDetail(BaseModel):
    product_id: str
    product_name: str
    quantity: int = 1
    price: float = 0.0
    freight_value: float = 0.0
    # Full olist_order_items + olist_products field coverage
    shipping_limit_date: str | None = None
    seller_id: str | None = None
    product_category: str | None = None
    product_photos_qty: int | None = None
    product_name_length: int | None = None
    product_description_length: int | None = None
    product_weight_g: float | None = None
    product_length_cm: float | None = None
    product_height_cm: float | None = None
    product_width_cm: float | None = None


class OrderPaymentDetail(BaseModel):
    """Full olist_order_payments row coverage."""

    payment_sequential: int = 1
    payment_type: str | None = None
    payment_installments: int = 1
    payment_value: float = 0.0


class OrderReviewDetail(BaseModel):
    """Full olist_order_reviews row coverage."""

    review_id: str | None = None
    review_score: int | None = None
    review_comment_title: str | None = None
    review_comment_message: str | None = None
    review_creation_date: str | None = None
    review_answer_timestamp: str | None = None


class OrderDetail(BaseModel):
    order_id: str
    customer_id: str
    customer_unique_id: str | None = None
    customer_city: str | None = None
    customer_state: str | None = None
    customer_zip_code_prefix: int | None = None
    status: str
    total: float
    items: list[OrderItemDetail] = Field(default_factory=list)
    payments: list[OrderPaymentDetail] = Field(default_factory=list)
    review: OrderReviewDetail | None = None
    purchase_timestamp: str | None = None
    approved_at: str | None = None
    delivered_carrier_date: str | None = None
    delivered_customer_date: str | None = None
    estimated_delivery_date: str | None = None
    tracking_number: str | None = None


class ReturnEligibilityResult(BaseModel):
    order_id: str
    is_eligible: bool
    status: str
    message: str
    delivered_date: str | None = None
    return_window_days: int = 30


class ReturnRequestResult(BaseModel):
    order_id: str
    rma_number: str
    refund_amount: float
    reason: str
    status: str = "APPROVED"
    created_at: str
    instructions: str


class ShipmentTrackingEvent(BaseModel):
    timestamp: str
    status: str
    location: str
    description: str


class ShipmentTrackingResult(BaseModel):
    order_id: str
    tracking_number: str
    carrier: str
    status: str
    estimated_delivery: str | None = None
    events: list[ShipmentTrackingEvent] = Field(default_factory=list)


class OrdersQueryResponse(BaseModel):
    intent: str
    order_id: str | None = None
    result: str
    raw_data: dict[str, Any] | None = None
    success: bool = True


# ── LangGraph Agent State (ecommerce-ai-agent) ──────────────────


class OrdersAgentState(TypedDict, total=False):
    """The state carried through each node in the Orders LangGraph."""

    messages: list
    intent: str  # order_status | product_lookup | shipping_tracking | return_request | return_policy | analytics_query | order_value_query | order_period_query | search_orders | general
    order_id: str
    product_id: str
    search_query: str
    tracking_number: str
    customer_email: str
    period_group_by: str  # "month" | "year" — for order_period_query
    tool_results: dict[str, Any]
    final_response: str
    retry_count: int
    error_message: str


# ── Operational Reporting Schemas (CommerceOS Architecture) ──────


class AutomationEligibility(BaseModel):
    eligible: bool = False
    action_type: str | None = None
    requires_approval: bool = True
    reasoning: str = ""
    minimum_confidence_threshold: float = 0.0


class OrderSummary(BaseModel):
    total_orders: int = 0
    pending_orders: int = 0
    completed_orders: int = 0
    cancelled_orders: int = 0
    delayed_orders: int = 0
    fulfillment_rate_pct: float = 0.0
    cancellation_rate_pct: float = 0.0
    data_source: str = "BOTH"
    data_status: str = DataCategory.OBSERVED.value
    sample_count: int | None = None
    observation_period: str | None = None
    not_estimable_reason: str | None = None
    data_availability: dict[str, str] = Field(
        default_factory=lambda: {
            "total_orders": "OBSERVED",
            "cancellations": "OBSERVED",
            "processing_times": "CALCULATED",
            "delivery_times": "CALCULATED",
            "returns_rma": "CALCULATED",
        }
    )


class OrdersHealth(BaseModel):
    status: str = (
        "INSUFFICIENT_DATA"  # HEALTHY | NEEDS_ATTENTION | CRITICAL | INSUFFICIENT_DATA | NOT_ESTIMABLE
    )
    summary_message: str = "Insufficient data to assess orders health."
    active_issues_count: int = 0
    not_estimable_reason: str | None = None


class AgeDistributionBucket(BaseModel):
    label: str
    lower_bound_hours: float
    upper_bound_hours: float | None = None
    order_count: int
    pct_of_pending: float


class PendingQueue(BaseModel):
    pending_count: int = 0
    age_distribution: list[AgeDistributionBucket] = Field(default_factory=list)
    median_age_hours: float | None = None
    p75_age_hours: float | None = None
    p90_age_hours: float | None = None
    p95_age_hours: float | None = None
    max_age_hours: float | None = None
    anomalous_aging_count: int = 0
    empirical_outlier_fence_hours: float | None = None
    aging_over_48h: int = 0
    data_source: str = "BOTH"
    data_status: str = DataCategory.CALCULATED.value
    sample_count: int | None = None
    method: str | None = None
    not_estimable_reason: str | None = None


class CancellationRisk(BaseModel):
    cancellation_rate_pct: float = 0.0
    historical_baseline_rate_pct: float | None = None
    z_score: float | None = None
    risk_level: str = "LOW"
    predicted_cancellations: int = 0
    risk_drivers: list[str] = Field(default_factory=list)
    anomaly_score: float | None = None
    recommended_action: str | None = None
    data_source: str = "BOTH"
    data_status: str = DataCategory.CALCULATED.value
    sample_count: int | None = None
    method: str | None = None
    not_estimable_reason: str | None = None


class FulfillmentHealth(BaseModel):
    avg_processing_hours: float | None = None
    median_processing_hours: float | None = None
    p90_processing_hours: float | None = None
    avg_delivery_days: float | None = None
    median_delivery_days: float | None = None
    fulfillment_rate_pct: float = 0.0
    delay_rate_pct: float = 0.0
    historical_delay_rate_pct: float | None = None
    delay_z_score: float | None = None
    sla_health: str = "UNKNOWN"
    data_source: str = "BOTH"
    data_status: str = DataCategory.CALCULATED.value
    sample_count: int | None = None
    method: str | None = None
    not_estimable_reason: str | None = None


class OrderFinding(BaseModel):
    category: str
    severity: str = "MEDIUM"
    what_happened: str
    why_it_matters: str
    recommended_action: str
    evidence: str = Field("", description="Factual numeric evidence from database records")
    probable_cause: str = Field("", description="Identified root cause or dimension")
    affected_entities: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, description="Dynamic sample-size-aware confidence score")
    data_status: str = DataCategory.CALCULATED.value
    source: str = "orders"
    sample_count: int | None = None
    method: str | None = None
    not_estimable_reason: str | None = None
    automation_eligibility: AutomationEligibility | None = None


class OrdersForecast(BaseModel):
    title: str = "Orders Forecast"
    order_trend: str = "STABLE"
    trend_confidence: float = 0.0
    predicted_daily_volume: float | None = None
    volume_lower_bound: float | None = None
    volume_upper_bound: float | None = None
    backlog_risk: str = "LOW"
    orders_at_risk: int = 0
    cancellation_risk: str = "LOW"
    expected_cancellations: int = 0
    sla_health: str = "UNKNOWN"
    what_happened: str = ""
    why_it_matters: str = ""
    recommended_action: str = ""
    confidence: float = 0.0
    data_source: str = "BOTH"
    forecast_method: str = "INSUFFICIENT_DATA"
    sample_count: int | None = None
    observation_period: str | None = None
    not_estimable_reason: str | None = None


class InvestigationSummary(BaseModel):
    data_sources: list[str] = Field(default_factory=list)
    records_analyzed: int = 0
    dimensions_investigated: list[str] = Field(default_factory=list)
    signals_evaluated: int = 0
    entities_examined: int = 0
    findings_count: int = 0
    investigation_steps: list[str] = Field(default_factory=list)
    anomalies_detected: int = 0
    tools_executed: list[str] = Field(default_factory=list)


class OrdersAgentOutput(BaseModel):
    agent: str = "orders"
    execution_id: str | None = None
    snapshot_id: str | None = None
    timestamp: str
    confidence: float = 0.0
    summary: OrderSummary
    health: OrdersHealth
    pending_queue: PendingQueue
    cancellation_risk: CancellationRisk
    fulfillment_health: FulfillmentHealth
    findings: list[OrderFinding] = Field(default_factory=list)
    forecast: OrdersForecast | None = None
    investigation_summary: InvestigationSummary | None = None
    data_limitation: str = (
        "Order lifecycle metrics, return eligibility, and shipment tracking are derived from verified "
        "historical Olist and DataCo transaction records. All risk thresholds and anomaly flags are computed "
        "empirically from actual order distributions."
    )
