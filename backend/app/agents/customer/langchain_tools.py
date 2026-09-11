"""
LangChain tools for the customer support ReAct agent. Every tool reads the same
transaction database as the other agents, source-aware to the simulated clock.
Used by `create_react_agent` when a chat model is configured.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.agents.customer.tools import CustomerSupportTools
from app.agents.orders.tools import OrdersTools


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by its ID. Returns status, total, items, tracking number, customer city/state, and key dates."""
    detail = OrdersTools.lookup_order(order_id)
    if not detail:
        return f"Order '{order_id}' was not found."
    items = "; ".join(f"{it.product_name} x{it.quantity} (₹{it.price:.2f})" for it in detail.items) or "fulfilment package"
    return (
        f"Order {detail.order_id}: status {detail.status.upper()}, total ₹{detail.total:.2f}, "
        f"placed {(detail.purchase_timestamp or '')[:10]}, delivered {(detail.delivered_customer_date or 'not yet')[:10]}, "
        f"tracking {detail.tracking_number}, ships to {detail.customer_city or '?'}, {detail.customer_state or '?'}. Items: {items}."
    )


@tool
def track_shipment(order_id: str) -> str:
    """Get carrier, status, ETA, and dispatch milestones for an order's shipment."""
    r = OrdersTools.track_shipment(order_id)
    if r.status == "NOT_FOUND":
        return f"No shipment found for order '{order_id}'."
    events = "; ".join(f"{e.status} @ {e.location} ({e.timestamp[:10]})" for e in r.events) or "registered with carrier"
    return f"Carrier {r.carrier}, tracking {r.tracking_number}, status {r.status}, ETA {(r.estimated_delivery or 'on schedule')[:10]}. Milestones: {events}."


@tool
def check_return_eligibility(order_id: str) -> str:
    """Check whether an order is within the 30-day return window (based on delivery date vs the current clock)."""
    r = OrdersTools.check_return_eligibility(order_id)
    return f"{'ELIGIBLE' if r.is_eligible else 'INELIGIBLE'} ({r.status}): {r.message}"


@tool
def get_return_policy() -> str:
    """Return the store's return & refund policy."""
    return OrdersTools.get_return_policy()


@tool
def initiate_return(order_id: str, reason: str) -> str:
    """Create an RMA for an eligible order. Only call after check_return_eligibility says ELIGIBLE."""
    elig = OrdersTools.check_return_eligibility(order_id)
    if not elig.is_eligible:
        return f"Cannot create an RMA — order {order_id} is {elig.status}: {elig.message}"
    rma = OrdersTools.initiate_return(order_id, reason=reason or "Customer requested return")
    return f"RMA {rma.rma_number} created for order {order_id}: ₹{rma.refund_amount:.2f} refund. {rma.instructions}"


@tool
def billing_details(order_id: str) -> str:
    """Explain the charge on an order using real payment records (amount, method, installments)."""
    text, _ = CustomerSupportTools.billing_lookup(order_id)
    return text


@tool
def product_search(keyword: str) -> str:
    """Search the catalog by product ID or a category keyword (English or Portuguese). Returns matches with price and units sold."""
    text, _ = CustomerSupportTools.product_info(keyword)
    return text


@tool
def store_health() -> str:
    """High-level store operations snapshot: order volume, fulfilment %, delay %, SLA."""
    text, _ = CustomerSupportTools.pipeline_snapshot()
    return text


CUSTOMER_TOOLS = [
    lookup_order,
    track_shipment,
    check_return_eligibility,
    get_return_policy,
    initiate_return,
    billing_details,
    product_search,
    store_health,
]
