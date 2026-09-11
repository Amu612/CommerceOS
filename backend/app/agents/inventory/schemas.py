"""
Inventory Agent Schemas.
Provides data models for the Smart Inventory Watchdog Agent,
compatible with both LangGraph ReAct pipelines and the InventoryAgentView frontend.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class InventoryProduct(BaseModel):
    id: Optional[str] = None
    product_id: Optional[str] = None
    sku: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    stockQuantity: Optional[int] = None
    stock_quantity: Optional[int] = None
    available_stock: Optional[int] = None
    price: Optional[float] = 0.0
    weight_g: Optional[float] = None
    reorder_required: Optional[bool] = False
    reorder_flag: Optional[bool] = False
    lead_time_days: Optional[int] = 5


class ReorderRecommendation(BaseModel):
    product_id: Optional[str] = None
    sku: Optional[str] = None
    name: Optional[str] = None
    current_stock: Optional[int] = None
    stock_quantity: Optional[int] = None
    daily_sales: Optional[float] = None
    average_daily_sales: Optional[float] = None
    lead_time_days: Optional[int] = 5
    safety_stock: Optional[int] = None
    reorder_point: Optional[int] = None
    suggested_quantity: Optional[int] = None
    recommended_quantity: Optional[int] = None
    reorder_quantity: Optional[int] = None
    estimated_cost: Optional[float] = None
    supplier_unit_cost: Optional[float] = None
    reason: Optional[str] = None


class SalesAnalysis(BaseModel):
    product_id: Optional[str] = None
    sku: Optional[str] = None
    name: Optional[str] = None
    total_sales: Optional[int] = None
    average_daily_sales: Optional[float] = None
    sales_velocity: Optional[float] = None
    trend: Optional[str] = "STABLE"
    reorder_quantity: Optional[int] = None


class InventoryAlert(BaseModel):
    id: Optional[str] = None
    product_id: Optional[str] = None
    sku: Optional[str] = None
    name: Optional[str] = None
    severity: Optional[str] = "MEDIUM"  # LOW, MEDIUM, HIGH, CRITICAL
    message: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[str] = None
    timestamp: Optional[str] = None


class InventoryAction(BaseModel):
    action: Optional[str] = None
    status: Optional[str] = "COMPLETED"
    product_id: Optional[str] = None
    message: Optional[str] = None


class ToolCallRecord(BaseModel):
    tool: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[Any] = None


class InventoryAgentMetrics(BaseModel):
    total_products: int = 0
    low_stock_count: int = 0
    reorder_count: int = 0
    inventory_value: float = 0.0


class InventoryAgentResponse(BaseModel):
    output: Optional[str] = None
    execution_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    status: str = "SUCCESS"
    products: List[InventoryProduct] = Field(default_factory=list)
    low_stock_products: List[InventoryProduct] = Field(default_factory=list)
    recommendations: List[ReorderRecommendation] = Field(default_factory=list)
    reorder_suggestions: List[ReorderRecommendation] = Field(default_factory=list)
    sales_analysis: List[SalesAnalysis] = Field(default_factory=list)
    alerts: List[InventoryAlert] = Field(default_factory=list)
    actions: List[InventoryAction] = Field(default_factory=list)
    metrics: InventoryAgentMetrics = Field(default_factory=InventoryAgentMetrics)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)


class InventoryQueryRequest(BaseModel):
    query: Optional[str] = None
    message: Optional[str] = None
    product_id: Optional[str] = None
    threshold: Optional[int] = 50
    history: Optional[List[dict]] = None


class InventoryReorderRequest(BaseModel):
    product_id: str
    quantity: int
    supplier_notes: Optional[str] = None
