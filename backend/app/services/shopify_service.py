"""
Shopify live data source ingestion.

Two ways data gets in, both funnelling through `_ingest_shopify_record` (the
one and only Shopify -> `app.models.shopify` mapping, so there's a single
source of truth regardless of which path an order arrived by):

  - `pull_latest_orders()`  — polls Shopify's Admin REST API. Works with no
    public URL, so it's the reliable path for local dev / a demo. Call it
    manually (`POST /api/v1/shopify/sync`) or on a timer.
  - the `/api/v1/shopify/webhook` route (`app.api.v1.shopify`) — Shopify pushes
    `orders/create` / `orders/updated` / `orders/fulfilled` instantly, for
    whenever the app is reachable over HTTPS (deployed, or tunnelled). Not
    required for the app to work — a bonus once you have a public URL.

Every ingested/updated order publishes a `publish_event("data_source", ...)`
so the WebSocket + existing toast mechanism picks it up live, exactly like
the historic replay engine does for its own events.

Auth: Dev Dashboard apps use the OAuth client-credentials grant
(https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant) —
`_get_access_token()` exchanges SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET for a
24h Admin API access token, caches it in-process, and transparently requests
a fresh one shortly before it expires. No token is ever pasted manually
(SHOPIFY_ADMIN_TOKEN, if set, is only a fallback for the older admin-created
custom-app flow where Shopify issues a static, non-expiring token instead).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.shopify import ShopifyCustomer, ShopifyOrder, ShopifyOrderItem, ShopifyProduct
from app.services.event_bus import publish_event

logger = logging.getLogger(__name__)

# In-process token cache for the client-credentials grant. `expires_at` is a
# monotonic-clock deadline; refreshed 60s early, matching Shopify's own
# reference implementation.
_token_lock = threading.Lock()
_cached_token: Optional[str] = None
_token_expires_at: float = 0.0
_TOKEN_REFRESH_MARGIN_SECONDS = 60


def _parse_dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _admin_url(path: str) -> str:
    from app.core.settings import settings

    return f"https://{settings.SHOPIFY_STORE_DOMAIN}/admin/api/{settings.SHOPIFY_API_VERSION}/{path}"


def _fetch_client_credentials_token() -> str:
    """POSTs the client-credentials grant and returns a fresh access token.
    Raises httpx.HTTPError on failure — callers should let that surface as a
    clear sync error rather than silently falling back to an empty token."""
    from app.core.settings import settings

    resp = httpx.post(
        f"https://{settings.SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": settings.SHOPIFY_CLIENT_ID,
            "client_secret": settings.SHOPIFY_CLIENT_SECRET,
        },
        timeout=20.0,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise httpx.HTTPError(f"Shopify token exchange returned no access_token: {body}")
    expires_in = float(body.get("expires_in") or 86399)

    global _cached_token, _token_expires_at
    with _token_lock:
        _cached_token = token
        _token_expires_at = time.monotonic() + expires_in - _TOKEN_REFRESH_MARGIN_SECONDS
    logger.info("shopify_token_refreshed", extra={"expires_in": expires_in})
    return token


def _get_access_token() -> str:
    """Static SHOPIFY_ADMIN_TOKEN wins if set (legacy custom-app path);
    otherwise exchanges/caches/refreshes via the client-credentials grant."""
    from app.core.settings import settings

    if settings.SHOPIFY_ADMIN_TOKEN:
        return settings.SHOPIFY_ADMIN_TOKEN

    with _token_lock:
        still_valid = _cached_token and time.monotonic() < _token_expires_at
        if still_valid:
            return _cached_token  # type: ignore[return-value]

    return _fetch_client_credentials_token()


def _headers() -> dict:
    return {"X-Shopify-Access-Token": _get_access_token(), "Content-Type": "application/json"}


def _ingest_shopify_record(order_json: dict, db: Session) -> ShopifyOrder:
    """Upserts one Shopify order (+ its customer, line items, products) from
    the shape Shopify's REST API / webhooks both use. Returns the row."""
    oid = int(order_json["id"])
    customer_json = order_json.get("customer") or {}
    customer_id = int(customer_json["id"]) if customer_json.get("id") else None

    if customer_id is not None:
        cust = db.get(ShopifyCustomer, customer_id)
        if cust is None:
            cust = ShopifyCustomer(customer_id=customer_id)
            db.add(cust)
        cust.email = customer_json.get("email")
        cust.first_name = customer_json.get("first_name")
        cust.last_name = customer_json.get("last_name")
        addr = customer_json.get("default_address") or {}
        cust.city = addr.get("city")
        cust.state = addr.get("province")
        cust.country = addr.get("country")
        cust.orders_count = customer_json.get("orders_count") or cust.orders_count or 0
        cust.total_spent = float(customer_json.get("total_spent") or cust.total_spent or 0.0)
        cust.created_at = _parse_dt(customer_json.get("created_at")) or cust.created_at

    shipping = order_json.get("shipping_address") or {}
    fulfillments = order_json.get("fulfillments") or []
    tracking_number = fulfillments[0].get("tracking_number") if fulfillments else None
    tracking_company = fulfillments[0].get("tracking_company") if fulfillments else None

    order = db.get(ShopifyOrder, oid)
    is_new = order is None
    if order is None:
        order = ShopifyOrder(order_id=oid)
        db.add(order)
    order.order_number = str(order_json.get("order_number") or order_json.get("name") or oid)
    order.customer_id = customer_id
    order.email = order_json.get("email")
    order.financial_status = order_json.get("financial_status")
    order.fulfillment_status = order_json.get("fulfillment_status")
    order.currency = order_json.get("currency")
    order.total_price = float(order_json.get("total_price") or 0.0)
    order.total_tax = float(order_json.get("total_tax") or 0.0)
    order.total_discounts = float(order_json.get("total_discounts") or 0.0)
    shipping_lines = order_json.get("shipping_lines") or []
    order.shipping_price = sum(float(s.get("price") or 0.0) for s in shipping_lines)
    order.shipping_city = shipping.get("city")
    order.shipping_state = shipping.get("province")
    order.shipping_country = shipping.get("country")
    order.created_at = _parse_dt(order_json.get("created_at")) or datetime.now(timezone.utc)
    order.updated_at = _parse_dt(order_json.get("updated_at"))
    order.processed_at = _parse_dt(order_json.get("processed_at"))
    order.cancelled_at = _parse_dt(order_json.get("cancelled_at"))
    order.closed_at = _parse_dt(order_json.get("closed_at"))
    order.tracking_number = tracking_number
    order.tracking_company = tracking_company
    order.test = bool(order_json.get("test"))

    for li in order_json.get("line_items") or []:
        line_id = int(li["id"])
        product_id = int(li["product_id"]) if li.get("product_id") else None
        if product_id is not None and db.get(ShopifyProduct, product_id) is None:
            db.add(ShopifyProduct(
                product_id=product_id,
                title=li.get("title") or li.get("name"),
                product_type=li.get("product_type") or li.get("vendor"),
                price=float(li.get("price") or 0.0),
            ))
        item = db.get(ShopifyOrderItem, line_id)
        if item is None:
            item = ShopifyOrderItem(line_item_id=line_id, order_id=oid)
            db.add(item)
        item.product_id = product_id
        item.title = li.get("title") or li.get("name")
        item.quantity = int(li.get("quantity") or 1)
        item.price = float(li.get("price") or 0.0)
        item.total_discount = float(li.get("total_discount") or 0.0)

    db.commit()

    publish_event("data_source", {
        "type": "shopify_order_synced",
        "order_id": oid,
        "order_number": order.order_number,
        "is_new": is_new,
        "financial_status": order.financial_status,
        "fulfillment_status": order.fulfillment_status,
        "total_price": order.total_price,
        "source": "shopify",
    })
    return order


def pull_products(limit: int = 100) -> dict[str, Any]:
    """
    Polls Shopify's Products API for real on-hand inventory counts. Order
    ingestion alone only ever creates a *stub* ShopifyProduct (from line-item
    text, no quantity) the first time a product is sold — this is the only
    path that actually populates `inventory_quantity`, which is what makes
    the Inventory agent's live numbers OBSERVED rather than blank.
    """
    from app.core.settings import settings

    if not settings.shopify_configured:
        return {"status": "NOT_CONFIGURED", "synced": 0, "reason": "Shopify store domain / admin token not set."}

    try:
        resp = httpx.get(_admin_url("products.json"), headers=_headers(), params={"limit": min(limit, 250)}, timeout=20.0)
        resp.raise_for_status()
        products = resp.json().get("products", [])
    except httpx.HTTPError as exc:
        logger.warning("shopify_products_pull_failed", extra={"error": str(exc)})
        return {"status": "ERROR", "synced": 0, "reason": str(exc)}

    db = SessionLocal()
    synced = 0
    try:
        for prod_json in products:
            pid = int(prod_json["id"])
            variants = prod_json.get("variants") or []
            price = float(variants[0].get("price") or 0.0) if variants else 0.0
            on_hand = sum(int(v.get("inventory_quantity") or 0) for v in variants) if variants else None

            product = db.get(ShopifyProduct, pid)
            if product is None:
                product = ShopifyProduct(product_id=pid)
                db.add(product)
            product.title = prod_json.get("title")
            product.product_type = prod_json.get("product_type") or prod_json.get("vendor")
            product.vendor = prod_json.get("vendor")
            product.price = price
            product.inventory_quantity = on_hand
            product.created_at = _parse_dt(prod_json.get("created_at")) or product.created_at
            synced += 1
        db.commit()
    finally:
        db.close()

    return {"status": "OK", "synced": synced, "fetched": len(products)}


def pull_latest_orders(limit: int = 50) -> dict[str, Any]:
    """Polls the Shopify Admin REST API for the most recently updated orders
    and upserts them. Returns a small summary dict for the API response."""
    from app.core.settings import settings

    if not settings.shopify_configured:
        return {"status": "NOT_CONFIGURED", "synced": 0, "reason": "Shopify store domain / admin token not set."}

    params = {"status": "any", "limit": min(limit, 250), "order": "updated_at desc"}
    try:
        resp = httpx.get(_admin_url("orders.json"), headers=_headers(), params=params, timeout=20.0)
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
    except httpx.HTTPError as exc:
        logger.warning("shopify_pull_failed", extra={"error": str(exc)})
        return {"status": "ERROR", "synced": 0, "reason": str(exc)}

    db = SessionLocal()
    synced = 0
    try:
        for order_json in orders:
            _ingest_shopify_record(order_json, db)
            synced += 1
    finally:
        db.close()

    products_result = pull_products()
    return {
        "status": "OK",
        "synced": synced,
        "fetched": len(orders),
        "products_synced": products_result.get("synced", 0),
    }
