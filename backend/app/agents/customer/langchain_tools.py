"""
LangChain tools for the customer support ReAct agent. Every tool reads the same
transaction database as the other agents, source-aware to the simulated clock.
Used by `create_react_agent` when a chat model is configured.
"""
from __future__ import annotations

from langchain_core.tools import tool

from app.agents.customer.tools import CustomerSupportTools
from app.agents.orders.tools import OrdersTools
from app.agents.entity_resolver import entity_resolver


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by its ID. Returns status, total, items, tracking number, customer city/state, and key dates."""
    detail = OrdersTools.lookup_order(order_id)
    if not detail:
        # Cross-table fallback: if user provided a customer, seller, product, or review ID instead
        fallback = entity_resolver.resolve_entity(order_id)
        if fallback.get("status") == "FOUND":
            return fallback.get("summary", f"Found entity of type {fallback.get('entity_type')}.")
        return f"Order '{order_id}' was not found."
    items = "; ".join(f"{it.product_name} x{it.quantity} (R${it.price:.2f})" for it in detail.items) or "fulfilment package"
    return (
        f"Order {detail.order_id}: status {detail.status.upper()}, total R${detail.total:.2f}, "
        f"placed {(detail.purchase_timestamp or '')[:10]}, delivered {(detail.delivered_customer_date or 'not yet')[:10]}, "
        f"tracking {detail.tracking_number}, ships to {detail.customer_city or '?'}, {detail.customer_state or '?'}. Items: {items}."
    )


@tool
def lookup_customer(customer_id: str) -> str:
    """Look up customer profile, location, lifetime spend, and all associated orders using customer_id or customer_unique_id (or DataCo customer ID)."""
    res = entity_resolver.resolve_entity(customer_id)
    if res.get("status") == "FOUND" and res.get("entity_type") == "customer":
        return res.get("summary", "Customer found.")
    # If not directly matched as customer, check if it resolves to any entity
    if res.get("status") == "FOUND":
        return f"ID '{customer_id}' is a {res.get('entity_type')}, not a customer:\n" + res.get("summary", "")
    return f"Customer '{customer_id}' was not found in database records."


@tool
def lookup_seller(seller_id: str) -> str:
    """Look up seller location, order items fulfilled, and catalog items using seller_id."""
    res = entity_resolver.resolve_entity(seller_id)
    if res.get("status") == "FOUND" and res.get("entity_type") == "seller":
        return res.get("summary", "Seller found.")
    if res.get("status") == "FOUND":
        return f"ID '{seller_id}' is a {res.get('entity_type')}, not a seller:\n" + res.get("summary", "")
    return f"Seller '{seller_id}' was not found in database records."


@tool
def lookup_review(review_id: str) -> str:
    """Look up customer review details, star rating, comment title and message, and linked order using review_id."""
    res = entity_resolver.resolve_entity(review_id)
    if res.get("status") == "FOUND" and res.get("entity_type") == "review":
        return res.get("summary", "Review found.")
    if res.get("status") == "FOUND":
        return f"ID '{review_id}' is a {res.get('entity_type')}, not a review:\n" + res.get("summary", "")
    return f"Review '{review_id}' was not found in database records."


@tool
def resolve_unknown_id(entity_id: str) -> str:
    """Universal entity lookup: inspects orders, customers, products, sellers, and reviews to identify what entity this ID belongs to and returns all linked data."""
    res = entity_resolver.resolve_entity(entity_id)
    return res.get("summary", f"No record matching ID '{entity_id}' found.")


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
    return f"RMA {rma.rma_number} created for order {order_id}: R${rma.refund_amount:.2f} refund. {rma.instructions}"


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
    lookup_customer,
    lookup_seller,
    lookup_review,
    resolve_unknown_id,
    track_shipment,
    check_return_eligibility,
    get_return_policy,
    initiate_return,
    billing_details,
    product_search,
    store_health,
]
