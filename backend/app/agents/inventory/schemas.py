"""
Inventory Agent Schemas.
Provides data models for the Smart Inventory Watchdog Agent,
compatible with both LangGraph ReAct pipelines and the InventoryAgentView frontend.
"""

from typing import Any

from pydantic import BaseModel, Field


class InventoryProduct(BaseModel):
    id: str | None = None
    product_id: str | None = None
    sku: str | None = None
    name: str | None = None
    category: str | None = None
    stockQuantity: int | None = None
    stock_quantity: int | None = None
    available_stock: int | None = None
    current_stock: int | None = None
    safety_stock: int | None = None
    reorder_point: int | None = None
    daily_sales: float | None = None
    price: float | None = 0.0
    weight_g: float | None = None
    reorder_required: bool | None = False
    reorder_flag: bool | None = False
    lead_time_days: int | None = 5


class ReorderRecommendation(BaseModel):
    product_id: str | None = None
    sku: str | None = None
    name: str | None = None
    current_stock: int | None = None
    stock_quantity: int | None = None
    daily_sales: float | None = None
    average_daily_sales: float | None = None
    lead_time_days: int | None = 5
    safety_stock: int | None = None
    reorder_point: int | None = None
    suggested_quantity: int | None = None
    recommended_quantity: int | None = None
    reorder_quantity: int | None = None
    estimated_cost: float | None = None
    supplier_unit_cost: float | None = None
    reason: str | None = None


class SalesAnalysis(BaseModel):
    product_id: str | None = None
    sku: str | None = None
    name: str | None = None
    total_sales: int | None = None
    average_daily_sales: float | None = None
    sales_velocity: float | None = None
    trend: str | None = "STABLE"
    reorder_quantity: int | None = None


class InventoryAlert(BaseModel):
    id: str | None = None
    product_id: str | None = None
    sku: str | None = None
    name: str | None = None
    severity: str | None = "MEDIUM"  # LOW, MEDIUM, HIGH, CRITICAL
    message: str | None = None
    reason: str | None = None
    created_at: str | None = None
    timestamp: str | None = None


class InventoryAction(BaseModel):
    action: str | None = None
    status: str | None = "COMPLETED"
    product_id: str | None = None
    message: str | None = None


class ToolCallRecord(BaseModel):
    tool: str | None = None
    name: str | None = None
    input: Any | None = None
    output: Any | None = None


class InventoryAgentMetrics(BaseModel):
    total_products: int = 0
    low_stock_count: int = 0
    reorder_count: int = 0
    inventory_value: float = 0.0


class InventoryAgentResponse(BaseModel):
    output: str | None = None
    execution_id: str | None = None
    snapshot_id: str | None = None
    status: str = "SUCCESS"
    products: list[InventoryProduct] = Field(default_factory=list)
    low_stock_products: list[InventoryProduct] = Field(default_factory=list)
    recommendations: list[ReorderRecommendation] = Field(default_factory=list)
    reorder_suggestions: list[ReorderRecommendation] = Field(default_factory=list)
    sales_analysis: list[SalesAnalysis] = Field(default_factory=list)
    alerts: list[InventoryAlert] = Field(default_factory=list)
    actions: list[InventoryAction] = Field(default_factory=list)
    metrics: InventoryAgentMetrics = Field(default_factory=InventoryAgentMetrics)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


class InventoryQueryRequest(BaseModel):
    query: str | None = None
    message: str | None = None
    product_id: str | None = None
    threshold: int | None = 50
    history: list[dict] | None = None


class InventoryReorderRequest(BaseModel):
    product_id: str
    quantity: int
    supplier_notes: str | None = None
