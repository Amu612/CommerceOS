"""
Orders Agent Schemas — Adapted from m-peker/ecommerce-ai-agent.
Provides data models for both the LangGraph ReAct conversation pipeline
and empirical operational intelligence reporting.
"""
from enum import Enum
from typing import List, Optional, Dict, Any, Annotated
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class DataCategory(str, Enum):
    OBSERVED = "OBSERVED"        # Direct database read
    CALCULATED = "CALCULATED"    # Derived from observed values
    ESTIMATED = "ESTIMATED"      # Statistical inference
    MODELLED = "MODELLED"        # Algorithmic or regression model output
    UNAVAILABLE = "UNAVAILABLE"  # Structurally absent from schema
    NOT_ESTIMABLE = "NOT_ESTIMABLE"  # Cannot be computed mathematically from available records


class MetricProvenance(BaseModel):
    value: Optional[float] = None
    method: str
    sample_count: int = 0
    observation_period: Optional[str] = None
    data_status: str = DataCategory.CALCULATED.value
    not_estimable_reason: Optional[str] = None



# ── Interactive Orders Schemas (ecommerce-ai-agent) ─────────────

class OrderItemDetail(BaseModel):
    product_id: str
    product_name: str
    quantity: int = 1
    price: float = 0.0
    freight_value: float = 0.0


class OrderDetail(BaseModel):
    order_id: str
    customer_id: str
    customer_city: Optional[str] = None
    customer_state: Optional[str] = None
    status: str
    total: float
    items: List[OrderItemDetail] = Field(default_factory=list)
    purchase_timestamp: Optional[str] = None
    delivered_carrier_date: Optional[str] = None
    delivered_customer_date: Optional[str] = None
    estimated_delivery_date: Optional[str] = None
    tracking_number: Optional[str] = None


class ReturnEligibilityResult(BaseModel):
    order_id: str
    is_eligible: bool
    status: str
    message: str
    delivered_date: Optional[str] = None
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
    estimated_delivery: Optional[str] = None
    events: List[ShipmentTrackingEvent] = Field(default_factory=list)


class OrdersQueryResponse(BaseModel):
    intent: str
    order_id: Optional[str] = None
    result: str
    raw_data: Optional[Dict[str, Any]] = None
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
    tool_results: Dict[str, Any]
    final_response: str
    retry_count: int
    error_message: str


# ── Operational Reporting Schemas (CommerceOS Architecture) ──────

class AutomationEligibility(BaseModel):
    eligible: bool = False
    action_type: Optional[str] = None
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
    sample_count: Optional[int] = None
    observation_period: Optional[str] = None
    not_estimable_reason: Optional[str] = None
    data_availability: Dict[str, str] = Field(
        default_factory=lambda: {
            "total_orders": "OBSERVED",
            "cancellations": "OBSERVED",
            "processing_times": "CALCULATED",
            "delivery_times": "CALCULATED",
            "returns_rma": "CALCULATED",
        }
    )


class OrdersHealth(BaseModel):
    status: str = "INSUFFICIENT_DATA"   # HEALTHY | NEEDS_ATTENTION | CRITICAL | INSUFFICIENT_DATA | NOT_ESTIMABLE
    summary_message: str = "Insufficient data to assess orders health."
    active_issues_count: int = 0
    not_estimable_reason: Optional[str] = None


class AgeDistributionBucket(BaseModel):
    label: str
    lower_bound_hours: float
    upper_bound_hours: Optional[float] = None
    order_count: int
    pct_of_pending: float


class PendingQueue(BaseModel):
    pending_count: int = 0
    age_distribution: List[AgeDistributionBucket] = Field(default_factory=list)
    median_age_hours: Optional[float] = None
    p75_age_hours: Optional[float] = None
    p90_age_hours: Optional[float] = None
    p95_age_hours: Optional[float] = None
    max_age_hours: Optional[float] = None
    anomalous_aging_count: int = 0
    empirical_outlier_fence_hours: Optional[float] = None
    aging_over_48h: int = 0
    data_source: str = "BOTH"
    data_status: str = DataCategory.CALCULATED.value
    sample_count: Optional[int] = None
    method: Optional[str] = None
    not_estimable_reason: Optional[str] = None


class CancellationRisk(BaseModel):
    cancellation_rate_pct: float = 0.0
    historical_baseline_rate_pct: Optional[float] = None
    z_score: Optional[float] = None
    risk_level: str = "LOW"
    predicted_cancellations: int = 0
    risk_drivers: List[str] = Field(default_factory=list)
    anomaly_score: Optional[float] = None
    recommended_action: Optional[str] = None
    data_source: str = "BOTH"
    data_status: str = DataCategory.CALCULATED.value
    sample_count: Optional[int] = None
    method: Optional[str] = None
    not_estimable_reason: Optional[str] = None


class FulfillmentHealth(BaseModel):
    avg_processing_hours: Optional[float] = None
    median_processing_hours: Optional[float] = None
    p90_processing_hours: Optional[float] = None
    avg_delivery_days: Optional[float] = None
    median_delivery_days: Optional[float] = None
    fulfillment_rate_pct: float = 0.0
    delay_rate_pct: float = 0.0
    historical_delay_rate_pct: Optional[float] = None
    delay_z_score: Optional[float] = None
    sla_health: str = "UNKNOWN"
    data_source: str = "BOTH"
    data_status: str = DataCategory.CALCULATED.value
    sample_count: Optional[int] = None
    method: Optional[str] = None
    not_estimable_reason: Optional[str] = None


class OrderFinding(BaseModel):
    category: str
    severity: str = "MEDIUM"
    what_happened: str
    why_it_matters: str
    recommended_action: str
    evidence: str = Field("", description="Factual numeric evidence from database records")
    probable_cause: str = Field("", description="Identified root cause or dimension")
    affected_entities: List[str] = Field(default_factory=list)
    confidence: float = Field(0.0, description="Dynamic sample-size-aware confidence score")
    data_status: str = DataCategory.CALCULATED.value
    source: str = "orders"
    sample_count: Optional[int] = None
    method: Optional[str] = None
    not_estimable_reason: Optional[str] = None
    automation_eligibility: Optional[AutomationEligibility] = None


class OrdersForecast(BaseModel):
    title: str = "Orders Forecast"
    order_trend: str = "STABLE"
    trend_confidence: float = 0.0
    predicted_daily_volume: Optional[float] = None
    volume_lower_bound: Optional[float] = None
    volume_upper_bound: Optional[float] = None
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
    sample_count: Optional[int] = None
    observation_period: Optional[str] = None
    not_estimable_reason: Optional[str] = None



class InvestigationSummary(BaseModel):
    data_sources: List[str] = Field(default_factory=list)
    records_analyzed: int = 0
    dimensions_investigated: List[str] = Field(default_factory=list)
    signals_evaluated: int = 0
    entities_examined: int = 0
    findings_count: int = 0
    investigation_steps: List[str] = Field(default_factory=list)
    anomalies_detected: int = 0
    tools_executed: List[str] = Field(default_factory=list)


class OrdersAgentOutput(BaseModel):
    agent: str = "orders"
    execution_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    timestamp: str
    confidence: float = 0.0
    summary: OrderSummary
    health: OrdersHealth
    pending_queue: PendingQueue
    cancellation_risk: CancellationRisk
    fulfillment_health: FulfillmentHealth
    findings: List[OrderFinding] = Field(default_factory=list)
    forecast: Optional[OrdersForecast] = None
    investigation_summary: Optional[InvestigationSummary] = None
    data_limitation: str = (
        "Order lifecycle metrics, return eligibility, and shipment tracking are derived from verified "
        "historical Olist and DataCo transaction records. All risk thresholds and anomaly flags are computed "
        "empirically from actual order distributions."
    )
